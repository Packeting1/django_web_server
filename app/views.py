"""
Django视图模块 - 处理HTTP请求
"""

import json
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views import View
from asgiref.sync import async_to_sync
from .models import SystemConfig
from .utils import session_manager
from .funasr_client import FunASRClient
from .funasr_pool import get_connection_pool
from .llm_client import call_llm_simple
from .audio_processor import process_audio_data, get_audio_info

logger = logging.getLogger(__name__)

def index(request):
    """主页视图"""
    return render(request, 'app/index.html')

@csrf_exempt
@require_http_methods(["POST"])
def recognize_audio_api(request):
    """音频识别API（传统模式）"""
    if 'audio' not in request.FILES:
        return JsonResponse({
            "success": False,
            "error": "未提供音频文件"
        }, status=400)
    
    audio_file = request.FILES['audio']
    
    try:
        # 读取音频文件
        audio_data = audio_file.read()
        logger.info(f"接收音频文件: {audio_file.name}, 大小: {len(audio_data)} 字节")
        
        # 获取音频信息（用于调试）
        audio_info = get_audio_info(audio_data)
        logger.info(f"音频信息: {audio_info}")
        
        # 异步处理音频
        async def process_audio():
            # 处理音频数据
            pcm_data, sample_rate = process_audio_data(audio_data, audio_file.name or "")
            logger.info(f"处理后PCM数据大小: {len(pcm_data)} 字节, 采样率: {sample_rate}")
            
            # 语音识别
            funasr_client = FunASRClient()
            recognized_text = await funasr_client.recognize_audio(pcm_data, sample_rate)
            logger.info(f"识别结果: {recognized_text}")
            
            # 调用LLM（传统模式不保存历史记录）
            llm_response = ""
            if recognized_text:
                llm_response = await call_llm_simple(recognized_text, [])
                logger.info(f"LLM回答: {llm_response}")
            
            return {
                "success": True,
                "text": recognized_text,
                "llm_response": llm_response,
                "debug_info": {
                    "original_size": len(audio_data),
                    "processed_size": len(pcm_data),
                    "sample_rate": sample_rate,
                    "filename": audio_file.name,
                    "audio_info": audio_info
                }
            }
        
        # 运行异步函数
        result = async_to_sync(process_audio)()
        return JsonResponse(result)
        
    except Exception as e:
        logger.error(f"音频识别API错误: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)

@require_http_methods(["GET"])
def get_config(request):
    """获取系统配置"""
    try:
        config = SystemConfig.get_config()
        frontend_config = {
            "max_conversation_history": config.max_conversation_history
        }
        return JsonResponse(frontend_config)
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def cleanup_users(request):
    """清理非活跃用户"""
    try:
        data = json.loads(request.body) if request.body else {}
        inactive_hours = data.get('inactive_hours')
        
        cleaned_count = async_to_sync(session_manager.cleanup_inactive_sessions)(inactive_hours)
        remaining_users = async_to_sync(session_manager.get_user_count)()
        
        return JsonResponse({
            "success": True,
            "message": f"成功清理 {cleaned_count} 个非活跃用户会话",
            "cleaned_count": cleaned_count,
            "remaining_users": remaining_users
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            "success": False,
            "error": "无效的JSON数据"
        }, status=400)
    except Exception as e:
        logger.error(f"清理用户失败: {e}")
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)

@require_http_methods(["GET"])
def get_connection_pool_stats(request):
    """获取FunASR连接池状态"""
    async def get_stats():
        try:
            pool = await get_connection_pool()
            stats = pool.get_stats()
            return {
                "success": True,
                "stats": stats,
                "message": "连接池状态获取成功"
            }
        except Exception as e:
            logger.error(f"获取连接池状态失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    try:
        result = async_to_sync(get_stats)()
        return JsonResponse(result)
    except Exception as e:
        logger.error(f"连接池状态API错误: {e}")
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)


