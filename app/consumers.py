"""
WebSocket消费者模块 - 处理实时音频和对话
"""

import json
import asyncio
import logging
import base64
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.exceptions import StopConsumer
from .utils import session_manager
from .utils import clean_recognition_text
from .funasr_client import FunASRClient, create_stream_config_async
from .funasr_pool import get_connection_pool
from .llm_client import call_llm_stream, call_llm_simple
from .audio_processor import process_audio_data, get_audio_info

logger = logging.getLogger(__name__)

class StreamChatConsumer(AsyncWebsocketConsumer):
    """流式聊天WebSocket消费者"""
    
    async def connect(self):
        await self.accept()
        
        # 为每个连接分配唯一的用户ID
        self.user_id = await session_manager.create_session()
        logger.info(f"WebSocket流式连接建立，用户ID: {self.user_id}")
        
        # 发送用户ID到前端
        user_count = await session_manager.get_user_count()
        await self.send(text_data=json.dumps({
            "type": "user_connected",
            "user_id": self.user_id,
            "active_users": user_count
        }))
        
        self.funasr_client = None
        self.funasr_task = None
        self.is_running = True
        self.asr_connected = False
        
        # 用于累积文本和状态管理
        self.accumulated_text = ""
        self.last_complete_text = ""
        self.is_ai_speaking = False
        
        # 连接到FunASR服务
        await self.connect_funasr()
    
    async def disconnect(self, close_code):
        self.is_running = False
        
        if self.funasr_task:
            self.funasr_task.cancel()
        
        # 根据配置决定如何处理连接
        if self.funasr_client:
            try:
                from .utils import get_system_config_async
                config = await get_system_config_async()
                
                if config.use_connection_pool:
                    # 连接池模式：释放连接
                    pool = await get_connection_pool()
                    await pool.release_connection(self.user_id)
                    logger.info(f"用户 {self.user_id} 已释放连接池连接")
                else:
                    # 独立连接模式：直接断开
                    await self.funasr_client.disconnect()
                    logger.info(f"用户 {self.user_id} 已断开独立FunASR连接")
            except Exception as e:
                logger.error(f"处理FunASR连接断开失败: {e}")
        
        # 清理用户会话
        await session_manager.remove_session(self.user_id)
        logger.info(f"WebSocket连接关闭，用户 {self.user_id} 会话已清理")
        
        raise StopConsumer()
    
    async def connect_funasr(self):
        """连接到FunASR服务（支持连接池和独立连接模式）"""
        try:
            # 获取配置，决定使用连接池还是独立连接
            from .utils import get_system_config_async
            config = await get_system_config_async()
            
            if config.use_connection_pool:
                # 连接池模式
                pool = await get_connection_pool()
                self.funasr_client = await pool.get_connection(self.user_id)
                
                if self.funasr_client is None:
                    raise Exception("连接池已满，无法获取FunASR连接")
                
                logger.info(f"用户 {self.user_id} 从连接池获取FunASR连接成功")
                
                # 发送ASR连接成功通知到前端
                pool_stats = pool.get_stats()
                await self.send(text_data=json.dumps({
                    "type": "asr_connected",
                    "message": "ASR服务器连接成功（连接池模式）",
                    "connection_mode": "pool",
                    "pool_stats": pool_stats
                }))
            else:
                # 独立连接模式
                self.funasr_client = FunASRClient()
                await self.funasr_client.connect()
                
                # 发送初始配置
                stream_config = await create_stream_config_async()
                await self.funasr_client.send_config(stream_config)
                logger.info(f"用户 {self.user_id} 成功创建独立FunASR连接")
                
                # 发送ASR连接成功通知到前端
                await self.send(text_data=json.dumps({
                    "type": "asr_connected",
                    "message": "ASR服务器连接成功（独立连接模式）",
                    "connection_mode": "independent",
                    "config": stream_config
                }))
            
            self.asr_connected = True
            
            # 启动FunASR响应处理任务
            self.funasr_task = asyncio.create_task(self.handle_funasr_responses())
            
        except Exception as asr_error:
            logger.error(f"用户 {self.user_id} 连接FunASR失败: {asr_error}")
            await self.send(text_data=json.dumps({
                "type": "asr_connection_failed",
                "message": "无法连接到ASR服务器，请检查服务状态",
                "error": str(asr_error)
            }))
    
    async def reconnect_funasr(self):
        """重新获取FunASR连接"""
        try:
            # 停止当前的响应处理任务
            if self.funasr_task and not self.funasr_task.done():
                self.funasr_task.cancel()
            
            # 释放当前连接
            if self.funasr_client:
                try:
                    from .utils import get_system_config_async
                    config = await get_system_config_async()
                    
                    if config.use_connection_pool:
                        pool = await get_connection_pool()
                        await pool.release_connection(self.user_id)
                    else:
                        await self.funasr_client.disconnect()
                except Exception as e:
                    logger.error(f"释放连接失败: {e}")
            
            # 重新从连接池获取连接
            await self.connect_funasr()
            logger.info(f"用户 {self.user_id} FunASR重连成功")
            
        except Exception as e:
            logger.error(f"用户 {self.user_id} FunASR重连失败: {e}")
            self.asr_connected = False
            await self.send(text_data=json.dumps({
                "type": "asr_reconnect_failed",
                "message": "ASR服务重连失败",
                "error": str(e)
            }))
    
    async def receive(self, text_data=None, bytes_data=None):
        """接收WebSocket消息"""
        try:
            if text_data:
                # 处理文本消息
                message = json.loads(text_data)
                message_type = message.get('type')
                
                if message_type == 'audio_data':
                    await self.handle_audio_data(message.get('data'))
                elif message_type == 'reset_conversation':
                    await self.handle_reset_conversation()
                elif message_type == 'test_llm':
                    await self.handle_test_llm()
                    
            elif bytes_data:
                # 处理二进制数据（直接的音频数据）
                await self.handle_binary_audio_data(bytes_data)
            
        except json.JSONDecodeError:
            logger.error("收到无效的JSON数据")
        except Exception as e:
            logger.error(f"处理WebSocket消息失败: {e}")
    
    async def handle_binary_audio_data(self, audio_data):
        """处理二进制音频数据"""
        if not self.asr_connected or not self.funasr_client:
            return
        
        try:
            # 检查连接状态
            if not self.funasr_client.is_connected():
                logger.warning(f"用户 {self.user_id} FunASR连接已断开，尝试重连...")
                await self.reconnect_funasr()
                return
            
            # 直接发送二进制音频数据到FunASR
            await self.funasr_client.send_audio_data(audio_data)
            
        except Exception as e:
            logger.error(f"处理二进制音频数据失败: {e}")
            # 连接失败时尝试重连
            await self.reconnect_funasr()
    
    async def handle_audio_data(self, audio_data_b64):
        """处理音频数据"""
        if not self.asr_connected or not self.funasr_client:
            return
        
        try:
            # 检查连接状态
            if not self.funasr_client.is_connected():
                logger.warning(f"用户 {self.user_id} FunASR连接已断开，尝试重连...")
                await self.reconnect_funasr()
                return
            
            # 解码Base64音频数据
            audio_data = base64.b64decode(audio_data_b64)
            
            # 发送音频数据到FunASR
            await self.funasr_client.send_audio_data(audio_data)
            
        except Exception as e:
            logger.error(f"处理音频数据失败: {e}")
            # 连接失败时尝试重连
            await self.reconnect_funasr()
    
    async def handle_funasr_responses(self):
        """处理FunASR的识别结果"""
        try:
            while self.is_running:
                try:
                    # 检查FunASR连接状态
                    if not self.funasr_client or not self.funasr_client.is_connected():
                        logger.warning(f"用户 {self.user_id} FunASR连接已断开，停止响应处理")
                        break
                    
                    data = await self.funasr_client.receive_message(timeout=1.0)
                    if data is None:
                        continue
                    
                    if "text" in data and not self.is_ai_speaking and self.is_running:
                        raw_text = data["text"]
                        mode = data.get("mode", "")
                        
                        if mode == "2pass-online":
                            # 实时结果，更新显示
                            self.accumulated_text = raw_text
                            display_text = clean_recognition_text(raw_text)
                            if self.is_running:
                                await self.send(text_data=json.dumps({
                                    "type": "recognition_partial",
                                    "text": display_text
                                }))
                        
                        elif mode == "2pass-offline" or mode == "offline":
                            # 最终结果，检查是否需要调用LLM
                            self.accumulated_text = raw_text
                            display_text = clean_recognition_text(raw_text)
                            
                            if raw_text != display_text:
                                logger.info(f"用户 {self.user_id} 识别结果: 原始='{raw_text}' → 显示='{display_text}', 模式: {mode}")
                            
                            # 检查是否有有效的新文本
                            if display_text and display_text.strip() and display_text != self.last_complete_text:
                                self.last_complete_text = display_text
                                
                                # 发送最终识别结果
                                if self.is_running:
                                    await self.send(text_data=json.dumps({
                                        "type": "recognition_final",
                                        "text": display_text
                                    }))
                                
                                # 调用LLM获取回答
                                await self.call_llm_and_respond(display_text)
                
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if self.is_running:
                        logger.error(f"处理FunASR响应失败: {e}")
                    # 发生异常时也退出循环
                    break
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"FunASR响应处理任务异常: {e}")
        finally:
            logger.info(f"用户 {self.user_id} FunASR响应处理任务结束")
    
    async def call_llm_and_respond(self, user_input):
        """调用LLM并发送响应"""
        try:
            self.is_ai_speaking = True
            
            # 获取对话历史
            conversation_history = await session_manager.get_conversation_history(self.user_id)
            
            # 发送AI开始回答的通知
            if self.is_running:
                await self.send(text_data=json.dumps({
                    "type": "ai_start",
                    "user_text": user_input,
                    "message": "AI正在思考..."
                }))
            
            # 流式调用LLM - 使用跳过方式的实时处理
            from .llm_client import filter_think_tags
            full_response = ""
            accumulated_chunks = ""
            in_think_block = False
            is_start_output = True  # flag: 是否还在开头输出状态
            pending_content = ""    # 暂存可能需要跳过的内容
            
            async for chunk in call_llm_stream(user_input, conversation_history):
                if not self.is_running:
                    break
                
                if chunk:  # 确保chunk不为空
                    full_response += chunk
                    
                    # 改进的逐字符跳过处理  
                    for char in chunk:
                        # 优先处理think块逻辑
                        if in_think_block:
                            # 在think块内，检查结束标签
                            if char == '<' and not pending_content:
                                pending_content = '<'
                            elif pending_content and len(pending_content) < 8:
                                pending_content += char
                                if pending_content == '</think>':
                                    in_think_block = False
                                    pending_content = ""
                                    logger.info(f"用户 {self.user_id} 遇到think结束标签，退出think块")
                                elif not '</think>'.startswith(pending_content):
                                    # 不是结束标签，重置暂存
                                    logger.debug(f"用户 {self.user_id} think块内非结束标签: {repr(pending_content)}")
                                    pending_content = ""
                            else:
                                # 超出长度，重置暂存
                                pending_content = ""
                            # think块内的所有字符都跳过（不发送）
                        elif is_start_output:
                            # 在开头状态（且不在think块内）
                            if char.isspace():
                                logger.info(f"用户 {self.user_id} 跳过开头空白字符: {repr(char)}")
                                continue
                            elif char == '<':
                                pending_content = '<'
                                continue
                            elif pending_content and len(pending_content) < 7:
                                pending_content += char
                                if pending_content == '<think>':
                                    in_think_block = True
                                    pending_content = ""
                                    logger.info(f"用户 {self.user_id} 在开头遇到think标签，开始跳过")
                                    continue
                                elif not '<think>'.startswith(pending_content):
                                    is_start_output = False
                                    logger.info(f"用户 {self.user_id} 开头遇到非think标签，结束跳过状态")
                                    await self.send(text_data=json.dumps({
                                        "type": "ai_chunk",
                                        "content": pending_content
                                    }))
                                    pending_content = ""
                                else:
                                    continue
                            else:
                                # 遇到其他字符，结束开头状态
                                is_start_output = False
                                logger.info(f"用户 {self.user_id} 遇到第一个有效字符: {repr(char)}")
                                content_to_send = pending_content + char if pending_content else char
                                await self.send(text_data=json.dumps({
                                    "type": "ai_chunk",
                                    "content": content_to_send
                                }))
                                pending_content = ""
                        else:
                            # 正常状态（非开头，非think块）
                            if char == '<' and not pending_content:
                                pending_content = '<'
                            elif pending_content and len(pending_content) < 7:
                                pending_content += char
                                if pending_content == '<think>':
                                    in_think_block = True
                                    pending_content = ""
                                    logger.info(f"用户 {self.user_id} 在正常状态遇到think标签，开始跳过")
                                elif not '<think>'.startswith(pending_content):
                                    await self.send(text_data=json.dumps({
                                        "type": "ai_chunk",
                                        "content": pending_content
                                    }))
                                    pending_content = ""
                            else:
                                await self.send(text_data=json.dumps({
                                    "type": "ai_chunk",
                                    "content": char
                                }))
                
                # 减少延迟
                await asyncio.sleep(0.005)  # 比原版更快
            
            # 处理可能剩余的暂存内容
            if pending_content and not in_think_block and not is_start_output:
                await self.send(text_data=json.dumps({
                    "type": "ai_chunk", 
                    "content": pending_content
                }))
            
            # 发送AI回答完成的通知
            if self.is_running:
                # 过滤掉think标签后保存到历史记录
                filtered_response = filter_think_tags(full_response)
                
                await self.send(text_data=json.dumps({
                    "type": "ai_complete",
                    "full_response": filtered_response
                }))
                
                # 保存对话历史（使用过滤后的内容）
                await session_manager.add_conversation(self.user_id, user_input, filtered_response)
            
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            if self.is_running:
                await self.send(text_data=json.dumps({
                    "type": "ai_error",
                    "error": "AI服务暂时不可用"
                }))
        finally:
            self.is_ai_speaking = False
    
    async def handle_reset_conversation(self):
        """处理重置对话"""
        await session_manager.reset_conversation(self.user_id)
        await self.send(text_data=json.dumps({
            "type": "conversation_reset",
            "message": "对话历史已重置"
        }))
    
    async def handle_test_llm(self):
        """处理LLM测试"""
        try:
            from .llm_client import test_llm_connection
            result = await test_llm_connection()
            await self.send(text_data=json.dumps({
                "type": "llm_test_result",
                "result": result
            }))
        except Exception as e:
            await self.send(text_data=json.dumps({
                "type": "llm_test_result",
                "result": {
                    "success": False,
                    "error": "测试失败",
                    "details": str(e)
                }
            }))

class UploadConsumer(AsyncWebsocketConsumer):
    """文件上传WebSocket消费者"""
    
    async def connect(self):
        await self.accept()
        logger.info("文件上传WebSocket连接建立")
    
    async def disconnect(self, close_code):
        logger.info("文件上传WebSocket连接关闭")
        raise StopConsumer()
    
    async def receive(self, text_data=None, bytes_data=None):
        """接收文件上传数据"""
        try:
            if text_data:
                # 处理文本消息
                message = json.loads(text_data)
                message_type = message.get('type')
                
                if message_type == 'upload_audio':
                    await self.handle_upload_audio(message)
            
            elif bytes_data:
                # 处理二进制文件数据
                await self.handle_binary_upload(bytes_data)
                
        except json.JSONDecodeError:
            logger.error("收到无效的JSON数据")
        except Exception as e:
            logger.error(f"处理文件上传失败: {e}")
    
    async def handle_binary_upload(self, audio_data):
        """处理二进制音频文件上传"""
        try:
            logger.info(f"收到二进制音频数据: {len(audio_data)} 字节")
            
            # 发送处理开始通知
            await self.send(text_data=json.dumps({
                "type": "file_received",
                "size": len(audio_data),
                "message": "开始处理音频文件..."
            }))
            
            # 获取音频信息
            audio_info = get_audio_info(audio_data)
            await self.send(text_data=json.dumps({
                "type": "processing",
                "message": f"音频信息: {audio_info['format']} 格式，大小: {audio_info['size']} 字节"
            }))
            
            # 处理音频数据
            pcm_data, sample_rate = process_audio_data(audio_data, "upload.wav")
            await self.send(text_data=json.dumps({
                "type": "processing", 
                "message": "音频处理完成，开始语音识别...",
                "processed_size": len(pcm_data),
                "sample_rate": sample_rate
            }))
            
            # 使用流式识别方法（2pass模式）处理二进制文件
            await self.stream_recognize_audio(pcm_data, sample_rate)
            
        except Exception as e:
            logger.error(f"处理二进制音频上传失败: {e}")
            await self.send(text_data=json.dumps({
                "type": "error",
                "message": f"处理失败: {str(e)}"
            }))

    async def stream_recognize_audio(self, audio_data, sample_rate):
        """流式识别音频文件（参考web_server实现）"""
        funasr_client = None
        accumulated_text = ""
        
        try:
            # 连接FunASR服务
            funasr_client = FunASRClient()
            await funasr_client.connect()
            
            await self.send(text_data=json.dumps({
                "type": "recognition_start",
                "message": "连接到FunASR服务，开始识别..."
            }))
            
            # 使用2pass模式进行流式识别
            config = {
                "mode": "2pass",
                "chunk_size": [5, 10, 5],
                "chunk_interval": 10,
                "audio_fs": sample_rate,
                "wav_name": "web_upload_stream",
                "wav_format": "pcm",
                "is_speaking": True,
                "hotwords": "",
                "itn": True
            }
            await funasr_client.send_config(config)
            
            # 启动识别结果接收任务
            async def handle_recognition_results():
                nonlocal accumulated_text
                
                while True:
                    try:
                        data = await funasr_client.receive_message(timeout=5.0)
                        if data is None:
                            continue
                            
                        if "text" in data and data["text"].strip():
                            raw_text = data["text"].strip()
                            display_text = clean_recognition_text(raw_text)
                            mode = data.get("mode", "")
                            
                            if mode == "2pass-online":
                                # 实时结果
                                await self.send(text_data=json.dumps({
                                    "type": "recognition_partial",
                                    "text": display_text,
                                    "mode": mode
                                }))
                                
                            elif mode == "2pass-offline" or mode == "offline":
                                # 最终结果
                                accumulated_text += raw_text
                                await self.send(text_data=json.dumps({
                                    "type": "recognition_segment",
                                    "text": display_text,
                                    "accumulated": clean_recognition_text(accumulated_text),
                                    "mode": mode
                                }))
                            
                            if raw_text != display_text:
                                logger.info(f"流式识别结果: 原始='{raw_text}' → 显示='{display_text}' (模式: {mode})")
                            else:
                                logger.info(f"流式识别结果: '{raw_text}' (模式: {mode})")
                        
                        if data.get("is_final", False):
                            logger.info("识别完成")
                            break
                            
                    except Exception as e:
                        logger.error(f"接收识别结果错误: {e}")
                        break
            
            # 启动结果接收任务
            result_task = asyncio.create_task(handle_recognition_results())
            
            # 发送音频数据
            stride = int(60 * 10 / 10 / 1000 * sample_rate * 2)
            chunk_num = max(1, (len(audio_data) - 1) // stride + 1)
            
            logger.info(f"开始发送音频数据，分割为 {chunk_num} 个块")
            
            for i in range(chunk_num):
                beg = i * stride
                chunk = audio_data[beg:beg + stride]
                
                if len(chunk) == 0:
                    continue
                    
                await funasr_client.send_audio_data(chunk)
                
                # 发送进度更新
                if (i + 1) % 50 == 0 or i == chunk_num - 1:
                    progress = (i + 1) / chunk_num * 100
                    await self.send(text_data=json.dumps({
                        "type": "upload_progress",
                        "progress": progress,
                        "current": i + 1,
                        "total": chunk_num
                    }))
                
                await asyncio.sleep(0.01)
            
            # 发送结束标志
            end_config = {"is_speaking": False}
            await funasr_client.send_config(end_config)
            
            await self.send(text_data=json.dumps({
                "type": "upload_complete",
                "message": "音频发送完成，等待最终识别结果..."
            }))
            
            # 等待识别完成
            await result_task
            
            # 调用LLM生成回复
            if accumulated_text.strip():
                await self.send(text_data=json.dumps({
                    "type": "llm_start",
                    "message": "开始AI回复生成..."
                }))
                
                try:
                    llm_response = ""
                    chunk_count = 0
                    is_start_output = True  # flag: 是否还在开头输出状态  
                    in_think_block = False
                    pending_content = ""
                    logger.info(f"[上传识别] 初始化跳过状态: is_start_output={is_start_output}, in_think_block={in_think_block}")
                    
                    async for chunk in call_llm_stream(accumulated_text.strip(), []):
                        chunk_count += 1
                        logger.info(f"[上传识别] 收到LLM chunk #{chunk_count}: {repr(chunk)}")
                        if chunk:
                            llm_response += chunk
                            
                            # 改进的逐字符跳过处理
                            for char in chunk:
                                # 优先处理think块逻辑
                                if in_think_block:
                                    # 在think块内，检查结束标签
                                    if char == '<' and not pending_content:
                                        pending_content = '<'
                                    elif pending_content and len(pending_content) < 8:
                                        pending_content += char
                                        if pending_content == '</think>':
                                            in_think_block = False
                                            pending_content = ""
                                            logger.info(f"[上传识别] 遇到think结束标签，退出think块")
                                            # think块结束后，继续处理后续字符
                                        elif not '</think>'.startswith(pending_content):
                                            # 不是结束标签，重置暂存
                                            logger.debug(f"[上传识别] think块内非结束标签: {repr(pending_content)}")
                                            pending_content = ""
                                    else:
                                        # 超出长度，重置暂存
                                        pending_content = ""
                                    # think块内的所有字符都跳过（不发送）
                                elif is_start_output:
                                    # 在开头状态（且不在think块内）
                                    if char.isspace():
                                        logger.info(f"[上传识别] 跳过开头空白字符: {repr(char)}")
                                        continue
                                    elif char == '<':
                                        pending_content = '<'
                                        continue
                                    elif pending_content and len(pending_content) < 7:
                                        pending_content += char
                                        if pending_content == '<think>':
                                            in_think_block = True
                                            pending_content = ""
                                            logger.info(f"[上传识别] 在开头遇到think标签，开始跳过")
                                            continue
                                        elif not '<think>'.startswith(pending_content):
                                            is_start_output = False
                                            logger.info(f"[上传识别] 开头遇到非think标签，结束跳过状态")
                                            await self.send(text_data=json.dumps({
                                                "type": "llm_chunk",
                                                "chunk": pending_content
                                            }))
                                            pending_content = ""
                                        else:
                                            continue
                                    else:
                                        # 遇到其他字符，结束开头状态
                                        is_start_output = False
                                        logger.info(f"[上传识别] 遇到第一个有效字符: {repr(char)}, 结束开头跳过状态")
                                        content_to_send = pending_content + char if pending_content else char
                                        logger.info(f"[上传识别] 发送第一个有效内容: {repr(content_to_send)}")
                                        await self.send(text_data=json.dumps({
                                            "type": "llm_chunk",
                                            "chunk": content_to_send
                                        }))
                                        pending_content = ""
                                else:
                                    # 正常状态（非开头，非think块）
                                    if char == '<' and not pending_content:
                                        pending_content = '<'
                                    elif pending_content and len(pending_content) < 7:
                                        pending_content += char
                                        if pending_content == '<think>':
                                            in_think_block = True
                                            pending_content = ""
                                            logger.info(f"[上传识别] 在正常状态遇到think标签，开始跳过")
                                        elif not '<think>'.startswith(pending_content):
                                            await self.send(text_data=json.dumps({
                                                "type": "llm_chunk",
                                                "chunk": pending_content
                                            }))
                                            pending_content = ""
                                    else:
                                        await self.send(text_data=json.dumps({
                                            "type": "llm_chunk",
                                            "chunk": char
                                        }))
                        else:
                            logger.warning(f"[上传识别] LLM chunk #{chunk_count} 为空或None")
                    
                    # 处理剩余的暂存内容
                    if pending_content and not in_think_block and not is_start_output:
                        await self.send(text_data=json.dumps({
                            "type": "llm_chunk",
                            "chunk": pending_content
                        }))
                    
                    logger.info(f"[上传识别] LLM流式响应完成，总共{chunk_count}个chunks，完整响应: '{llm_response}'")
                    
                    # 对完整响应应用think标签过滤
                    from .llm_client import filter_think_tags
                    filtered_response = filter_think_tags(llm_response)
                    logger.info(f"[上传识别] 过滤前: {repr(llm_response)}")
                    logger.info(f"[上传识别] 过滤后: {repr(filtered_response)}")
                    
                    await self.send(text_data=json.dumps({
                        "type": "llm_complete",
                        "recognized_text": clean_recognition_text(accumulated_text),
                        "llm_response": filtered_response
                    }))
                    
                except Exception as llm_error:
                    logger.error(f"LLM调用失败: {llm_error}")
                    await self.send(text_data=json.dumps({
                        "type": "llm_error",
                        "error": "AI服务暂时不可用"
                    }))
            
        except Exception as e:
            logger.error(f"流式识别错误: {e}")
            await self.send(text_data=json.dumps({
                "type": "error",
                "message": f"识别失败: {str(e)}"
            }))
        finally:
            if funasr_client:
                await funasr_client.disconnect()

    async def handle_upload_audio(self, message):
        """处理音频文件上传（Base64格式）"""
        try:
            # 获取音频数据
            audio_data_b64 = message.get('audio_data')
            filename = message.get('filename', 'uploaded_audio')
            
            if not audio_data_b64:
                await self.send(text_data=json.dumps({
                    "type": "upload_error",
                    "error": "缺少音频数据"
                }))
                return
            
            # 解码音频数据
            audio_data = base64.b64decode(audio_data_b64)
            
            # 发送处理开始通知
            await self.send(text_data=json.dumps({
                "type": "upload_progress",
                "message": "开始处理音频文件...",
                "filename": filename
            }))
            
            # 获取音频信息
            audio_info = get_audio_info(audio_data)
            await self.send(text_data=json.dumps({
                "type": "upload_progress",
                "message": f"音频信息: {audio_info['format']} 格式，大小: {audio_info['size']} 字节"
            }))
            
            # 处理音频数据
            pcm_data, sample_rate = process_audio_data(audio_data, filename)
            await self.send(text_data=json.dumps({
                "type": "upload_progress",
                "message": f"音频处理完成，开始语音识别..."
            }))
            
            # 使用离线识别方法，支持实时显示识别片段
            async def progress_callback(data):
                await self.send(text_data=json.dumps(data))
            
            funasr_client = FunASRClient()
            recognized_text = await funasr_client.recognize_audio(pcm_data, sample_rate, progress_callback)
            
            if recognized_text:
                await self.send(text_data=json.dumps({
                    "type": "upload_progress", 
                    "message": "语音识别完成，正在调用AI..."
                }))
                
                # 调用LLM
                from .llm_client import call_llm_simple
                llm_response = await call_llm_simple(recognized_text, [])
                
                await self.send(text_data=json.dumps({
                    "type": "upload_complete",
                    "recognized_text": recognized_text,
                    "llm_response": llm_response,
                    "debug_info": {
                        "original_size": len(audio_data),
                        "processed_size": len(pcm_data),
                        "sample_rate": sample_rate,
                        "filename": filename,
                        "audio_info": audio_info
                    }
                }))
            else:
                await self.send(text_data=json.dumps({
                    "type": "upload_error",
                    "error": "语音识别失败，未能识别到有效内容"
                }))
            return
        
        except Exception as e:
            logger.error(f"处理音频上传失败: {e}")
            await self.send(text_data=json.dumps({
                "type": "upload_error",
                "error": f"处理失败: {str(e)}"
            })) 