"""
LLM客户端模块 - 基于Django配置
"""

import re
import asyncio
import logging
from typing import List, Dict, AsyncGenerator
from openai import AsyncOpenAI
from .utils import get_system_config_async

logger = logging.getLogger(__name__)

def filter_think_tags(text: str) -> str:
    """
    移除<think></think>标签及其内容
    
    Args:
        text: 原始文本
        
    Returns:
        str: 过滤后的文本
    """
    # 移除think标签及其内容（支持多行和嵌套）
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # 清理多余的空白行
    text = re.sub(r'\n\s*\n', '\n', text)
    # 去除首尾空白
    return text.strip()

async def _get_openai_client():
    """获取配置好的OpenAI客户端"""
    config = await get_system_config_async()
    return AsyncOpenAI(
        api_key=config.llm_api_key,
        base_url=config.llm_api_base
    )

async def call_llm_simple(user_input: str, conversation_history: List[Dict] = None) -> str:
    """
    简单LLM调用（不使用流式）
    
    Args:
        user_input: 用户输入
        conversation_history: 对话历史
    
    Returns:
        LLM响应文本
    """
    if conversation_history is None:
        conversation_history = []
    
    config = await get_system_config_async()
    client = await _get_openai_client()
    
    # 构建消息列表
    messages = []
    
    # 添加系统提示
    messages.append({
        "role": "system",
        "content": "你是一个友善且有帮助的AI助手。请用简洁、准确的方式回答用户的问题。"
    })
    
    # 添加历史对话
    for conv in conversation_history:
        messages.append({"role": "user", "content": conv["user"]})
        messages.append({"role": "assistant", "content": conv["assistant"]})
    
    # 添加当前用户输入
    messages.append({"role": "user", "content": "/nothink " + user_input})
    
    logger.info(f"[非流式调用] 发送请求到LLM，模型: {config.llm_model}, 消息数: {len(messages)}")
    
    try:
        response = await client.chat.completions.create(
            model=config.llm_model,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
            stream=False
        )
        
        logger.info(f"[非流式调用] 收到响应: {response}")
        
        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content
            logger.info(f"[非流式调用] LLM响应内容: {content}")
            
            # 过滤掉think标签
            filtered_content = filter_think_tags(content)
            logger.info(f"[非流式调用] 过滤后内容: '{filtered_content}'")
            
            return filtered_content.strip() if filtered_content.strip() else "抱歉，我无法理解您的问题。"
        else:
            logger.error(f"[非流式调用] LLM响应格式错误: {response}")
            return "抱歉，我无法理解您的问题。"
                    
    except Exception as e:
        logger.error(f"[非流式调用] LLM API请求失败: {e}")
        return "服务暂时不可用，请稍后再试。"

async def call_llm_stream(user_input: str, conversation_history: List[Dict] = None) -> AsyncGenerator[str, None]:
    """
    流式LLM调用
    
    Args:
        user_input: 用户输入
        conversation_history: 对话历史
    
    Yields:
        LLM响应文本片段
    """
    if conversation_history is None:
        conversation_history = []
    
    config = await get_system_config_async()
    client = await _get_openai_client()
    
    # 构建消息列表
    messages = []
    
    # 添加系统提示
    messages.append({
        "role": "system",
        "content": "你是一个AI语音助手，请简洁自然的回答用户的问题。你可以参考之前的对话上下文来给出更好的回答。"
    })
    
    # 添加历史对话
    for conv in conversation_history:
        messages.append({"role": "user", "content": conv["user"]})
        messages.append({"role": "assistant", "content": conv["assistant"]})
    
    # 添加当前用户输入
    messages.append({"role": "user", "content": '/nothink ' + user_input})
    
    logger.info(f"[流式调用] 发送请求到LLM，模型: {config.llm_model}, 消息数: {len(messages)}")
    logger.info(f"[流式调用] 用户输入: {user_input}")
    logger.info(f"[流式调用] 历史对话数: {len(conversation_history)}")
    
    try:
        stream = await client.chat.completions.create(
            model=config.llm_model,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
            stream=True
        )
        
        logger.info(f"[流式调用] 开始处理流式响应...")
        
        chunk_count = 0
        
        # 实时流式处理 - 每收到chunk就立即yield，与原版web_server一致
        async for chunk in stream:
            chunk_count += 1
            logger.debug(f"[流式调用] 收到chunk #{chunk_count}: {chunk}")
            
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                logger.debug(f"[流式调用] Delta对象: {delta}")
                
                if delta.content:
                    content = delta.content
                    logger.debug(f"[流式调用] 实时输出内容: '{content}'")
                    yield content  # 立即yield，不等待收集完成
                else:
                    logger.debug(f"[流式调用] Delta中没有content字段")
            else:
                logger.debug(f"[流式调用] Chunk中没有choices或choices为空")
        
        logger.info(f"[流式调用] 流式响应完成，总共{chunk_count}个chunks")
        
        if chunk_count == 0:
            logger.warning("[流式调用] 没有收到任何chunks")
            yield "抱歉，我现在无法回答您的问题。"
                    
    except Exception as e:
        logger.error(f"[流式调用] LLM API流式请求失败: {e}")
        import traceback
        logger.error(f"[流式调用] 详细错误信息: {traceback.format_exc()}")
        yield "服务暂时不可用，请稍后再试。"

async def test_llm_connection() -> Dict[str, any]:
    """
    测试LLM连接
    
    Returns:
        测试结果字典
    """
    config = await get_system_config_async()
    client = await _get_openai_client()
    
    # 简单的测试消息
    messages = [
        {"role": "system", "content": "你是一个测试助手。"},
        {"role": "user", "content": "/nothink 请回复'连接测试成功'"}
    ]
    
    logger.info(f"[连接测试] 开始测试LLM连接，API: {config.llm_api_base}, 模型: {config.llm_model}")
    
    try:
        start_time = asyncio.get_event_loop().time()
        
        response = await client.chat.completions.create(
            model=config.llm_model,
            messages=messages,
            temperature=0.1,
            max_tokens=50,
            stream=False,
            timeout=15
        )
        
        end_time = asyncio.get_event_loop().time()
        response_time = round((end_time - start_time) * 1000, 2)  # 毫秒
        
        logger.info(f"[连接测试] 收到响应: {response}")
        
        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content
            logger.info(f"[连接测试] 响应内容: '{content}'")
            
            # 过滤掉think标签
            filtered_content = filter_think_tags(content)
            logger.info(f"[连接测试] 过滤后内容: '{filtered_content}'")
            
            return {
                "success": True,
                "response_time": response_time,
                "model": config.llm_model,
                "api_base": config.llm_api_base,
                "response": filtered_content.strip(),
                "message": "LLM连接测试成功"
            }
        else:
            logger.error(f"[连接测试] 响应格式错误: {response}")
            return {
                "success": False,
                "response_time": response_time,
                "error": "响应格式错误",
                "details": str(response)
            }
            
    except Exception as e:
        logger.error(f"[连接测试] 连接失败: {e}")
        import traceback
        logger.error(f"[连接测试] 详细错误信息: {traceback.format_exc()}")
        return {
            "success": False,
            "error": "连接失败",
            "details": str(e)
        } 