"""
FunASR客户端模块 - 基于Django配置
"""

import ssl
import json
import asyncio
import logging
import websockets
from typing import Optional, Dict, Any
from .utils import get_system_config, get_system_config_async

logger = logging.getLogger(__name__)

def create_stream_config() -> Dict[str, Any]:
    """创建FunASR流配置（同步版本）"""
    config = get_system_config()
    return {
        "mode": "2pass",
        "chunk_size": [5, 10, 5],
        "chunk_interval": 10,
        "wav_name": "stream",
        "is_speaking": True,
        "hotwords": ""
    }

async def create_stream_config_async() -> Dict[str, Any]:
    """创建FunASR流配置（异步版本）"""
    config = await get_system_config_async()
    return {
        "mode": "2pass",
        "chunk_size": [5, 10, 5],
        "chunk_interval": 10,
        "wav_name": "stream",
        "is_speaking": True,
        "hotwords": ""
    }

class FunASRClient:
    """FunASR WebSocket客户端"""
    
    def __init__(self):
        self.websocket = None
        self.config = None
        self.uri = None
    
    async def connect(self):
        """连接到FunASR服务器"""
        try:
            # 异步获取配置
            if not self.config:
                self.config = await get_system_config_async()
                self.uri = self.config.get_funasr_uri()
            
            # 如果使用SSL连接，根据配置决定是否验证证书
            ssl_context = None
            if self.uri.startswith('wss://'):
                ssl_context = ssl.create_default_context()
                if not self.config.funasr_ssl_verify:
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                    logger.info("使用SSL连接，已禁用证书验证")
                else:
                    logger.info("使用SSL连接，启用证书验证")
            
            self.websocket = await websockets.connect(self.uri, ssl=ssl_context)
            logger.info(f"已连接到FunASR服务器: {self.uri}")
        except Exception as e:
            logger.error(f"连接FunASR失败: {e}")
            raise
    
    async def disconnect(self):
        """断开连接"""
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            logger.info("已断开FunASR连接")
    
    async def send_config(self, config: Dict[str, Any]):
        """发送配置消息"""
        if not self.websocket:
            raise RuntimeError("未连接到FunASR服务器")
        
        message = json.dumps(config, ensure_ascii=False)
        await self.websocket.send(message)
        logger.debug(f"发送配置: {message}")
    
    async def send_audio_data(self, audio_data: bytes):
        """发送音频数据"""
        if not self.websocket:
            raise RuntimeError("未连接到FunASR服务器")
        
        await self.websocket.send(audio_data)
        logger.debug(f"发送音频数据: {len(audio_data)} 字节")
    
    async def receive_message(self, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
        """接收消息"""
        if not self.websocket:
            return None
        
        # 安全检查连接状态
        try:
            if hasattr(self.websocket, 'closed') and self.websocket.closed:
                return None
        except AttributeError:
            # 某些websockets版本没有closed属性，忽略检查
            pass
        
        try:
            # 使用asyncio.wait_for设置超时
            message = await asyncio.wait_for(self.websocket.recv(), timeout=timeout)
            
            # 处理二进制消息
            if isinstance(message, bytes):
                logger.debug(f"收到二进制消息: {len(message)} 字节")
                return None
            
            # 处理文本消息
            try:
                data = json.loads(message)
                logger.debug(f"收到文本消息: {data}")
                return data
            except json.JSONDecodeError:
                logger.warning(f"无法解析JSON消息: {message}")
                return None
                
        except asyncio.TimeoutError:
            # 超时是正常的，不记录错误
            return None
        except websockets.exceptions.ConnectionClosed:
            logger.warning("FunASR连接已关闭")
            # 标记连接为无效
            self.websocket = None
            return None
        except Exception as e:
            logger.error(f"接收消息错误: {e}")
            # 发生其他错误也标记连接为无效
            self.websocket = None
            return None
    
    async def recognize_audio(self, pcm_data: bytes, sample_rate: int = 16000, progress_callback=None) -> str:
        """识别音频文件（完整文件模式，参考FunASR官方HTML实现）"""
        try:
            await self.connect()
            
            # 使用官方HTML文件模式的配置：offline模式
            config = {
                "mode": "offline",  # 关键：文件模式使用offline而不是2pass
                "chunk_size": [5, 10, 5],
                "chunk_interval": 10,
                "audio_fs": sample_rate,
                "wav_name": "uploaded_audio",
                "wav_format": "pcm",
                "is_speaking": True,
                "hotwords": "",
                "itn": True
            }
            await self.send_config(config)
            
            # 使用官方HTML的固定块大小960字节（而不是动态计算的stride）
            chunk_size = 960
            
            logger.info(f"开始识别音频文件，使用offline模式，块大小: {chunk_size}，总长度: {len(pcm_data)} 字节")
            
            # 启动识别结果接收任务
            accumulated_text = ""
            recognition_complete = asyncio.Event()
            
            async def handle_recognition_results():
                nonlocal accumulated_text
                
                while True:
                    try:
                        data = await self.receive_message(timeout=5.0)
                        if data is None:
                            continue
                            
                        if "text" in data and data["text"].strip():
                            raw_text = data["text"].strip()
                            mode = data.get("mode", "")
                            
                            logger.debug(f"收到识别结果: '{raw_text}' (模式: {mode})")
                            
                            if mode == "offline" or mode == "2pass-offline":
                                # offline模式的结果，累积文本
                                accumulated_text += raw_text + " "
                                logger.info(f"识别片段: '{raw_text}' (累积长度: {len(accumulated_text)})")
                                
                                # 实时发送识别片段到前端
                                if progress_callback:
                                    from .utils import clean_recognition_text
                                    cleaned_text = clean_recognition_text(raw_text)
                                    cleaned_accumulated = clean_recognition_text(accumulated_text.strip())
                                    await progress_callback({
                                        "type": "recognition_segment", 
                                        "text": cleaned_text,
                                        "accumulated": cleaned_accumulated,
                                        "mode": mode
                                    })
                        
                        # 等待is_final信号，这是关键
                        if data.get("is_final", False):
                            logger.info("收到is_final信号，音频识别完成")
                            recognition_complete.set()
                            break
                            
                    except Exception as e:
                        logger.error(f"接收识别结果错误: {e}")
                        break
            
            # 启动结果接收任务
            result_task = asyncio.create_task(handle_recognition_results())
            
            # 按照官方HTML方式分块发送：固定960字节块
            pos = 0
            chunk_count = 0
            while pos < len(pcm_data):
                chunk = pcm_data[pos:pos + chunk_size]
                if len(chunk) == 0:
                    break
                    
                await self.send_audio_data(chunk)
                chunk_count += 1
                pos += chunk_size
                
                logger.debug(f"发送音频块 {chunk_count}, 大小: {len(chunk)} 字节")
                
            
            # 发送结束信号（模拟官方HTML的stop()调用）
            end_config = {
                "is_speaking": False
            }
            await self.send_config(end_config)
            logger.info(f"音频数据发送完成，共发送 {chunk_count} 个块，等待最终识别结果...")
            
            # 等待识别完成或超时
            try:
                await asyncio.wait_for(recognition_complete.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("等待识别结果超时")
            
            # 取消结果任务
            result_task.cancel()
            
            # 清理并返回最终结果
            from .utils import clean_recognition_text
            return clean_recognition_text(accumulated_text.strip())
            
        except Exception as e:
            logger.error(f"音频识别错误: {e}")
            return ""
        finally:
            await self.disconnect()
    
    async def send_end_signal(self):
        """发送结束信号"""
        if self.websocket:
            try:
                end_config = {
                    "is_speaking": False,
                    "wav_name": "stream_end"
                }
                await self.send_config(end_config)
                logger.debug("发送结束信号")
            except Exception as e:
                logger.error(f"发送结束信号失败: {e}")
    
    def is_connected(self) -> bool:
        """检查是否已连接"""
        if self.websocket is None:
            return False
        
        # 安全检查连接状态
        try:
            if hasattr(self.websocket, 'closed'):
                return not self.websocket.closed
            else:
                # 如果没有closed属性，假设连接有效
                return True
        except AttributeError:
            # 某些websockets版本没有closed属性，假设连接有效
            return True 