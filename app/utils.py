"""
工具模块 - 包含各种辅助功能
"""

import re
import uuid
import logging
from typing import Dict, List, Optional
from channels.db import database_sync_to_async
from .models import SystemConfig, UserSession

logger = logging.getLogger(__name__)

def get_system_config():
    """获取系统配置（同步版本，用于非异步上下文）"""
    return SystemConfig.get_config()

@database_sync_to_async
def get_system_config_async():
    """获取系统配置（异步版本）"""
    return SystemConfig.get_config()

def clean_recognition_text(text: str) -> str:
    """
    清理识别文本，移除语言标记和元数据前缀
    
    Args:
        text: 原始识别文本
        
    Returns:
        清理后的文本
    """
    if not text:
        return text
    
    # 移除语言和元数据标记，如 <|zh|><|NEUTRAL|><|Speech|>, <|yue|><|NEUTRAL|><|Speech|> 等
    # 匹配模式：<|任意字符|><|任意字符|><|任意字符|>
    cleaned_text = re.sub(r'<\|[^|]*\|><\|[^|]*\|><\|[^|]*\|>\s*', '', text)
    
    # 移除其他可能的标记格式
    cleaned_text = re.sub(r'<\|[^|]*\|>\s*', '', cleaned_text)
    
    # 移除开头和结尾的空白字符
    cleaned_text = cleaned_text.strip()
    
    return cleaned_text

class SessionManager:
    """Django版会话管理器"""
    
    @staticmethod
    @database_sync_to_async
    def create_session() -> str:
        """创建新用户会话，返回会话ID"""
        session_id = str(uuid.uuid4())
        UserSession.objects.create(session_id=session_id)
        count = UserSession.objects.count()
        logger.info(f"创建用户会话: {session_id}, 当前活跃用户数: {count}")
        return session_id
    
    @staticmethod
    @database_sync_to_async
    def get_session(session_id: str) -> Optional[UserSession]:
        """获取用户会话"""
        try:
            session = UserSession.objects.get(session_id=session_id)
            # 更新最后活跃时间
            session.save()  # 这会触发auto_now字段更新
            return session
        except UserSession.DoesNotExist:
            return None
    
    @staticmethod
    @database_sync_to_async
    def get_conversation_history(session_id: str) -> List[Dict]:
        """获取用户对话历史"""
        try:
            session = UserSession.objects.get(session_id=session_id)
            # 更新最后活跃时间
            session.save()
            return session.get_conversation_history()
        except UserSession.DoesNotExist:
            return []
    
    @staticmethod
    @database_sync_to_async
    def add_conversation(session_id: str, user_input: str, ai_response: str):
        """添加对话到用户历史"""
        try:
            session = UserSession.objects.get(session_id=session_id)
            session.add_conversation(user_input, ai_response)
            logger.info(f"用户 {session_id} 对话历史已更新，当前保存 {len(session.conversation_history)} 轮对话")
        except UserSession.DoesNotExist:
            logger.warning(f"尝试更新不存在的会话: {session_id}")
    
    @staticmethod
    @database_sync_to_async
    def reset_conversation(session_id: str):
        """重置用户对话历史"""
        try:
            session = UserSession.objects.get(session_id=session_id)
            session.reset_conversation()
            logger.info(f"用户 {session_id} 对话历史已重置")
        except UserSession.DoesNotExist:
            logger.warning(f"尝试重置不存在的会话: {session_id}")
    
    @staticmethod
    @database_sync_to_async
    def remove_session(session_id: str):
        """移除用户会话"""
        try:
            UserSession.objects.get(session_id=session_id).delete()
            count = UserSession.objects.count()
            logger.info(f"移除用户会话: {session_id}, 剩余活跃用户数: {count}")
        except UserSession.DoesNotExist:
            pass
    
    @staticmethod
    @database_sync_to_async
    def cleanup_inactive_sessions(inactive_hours: int = None):
        """清理非活跃的用户会话"""
        count = UserSession.cleanup_inactive_sessions(inactive_hours)
        if count > 0:
            logger.info(f"清理了 {count} 个非活跃用户会话")
        return count
    
    @staticmethod
    @database_sync_to_async
    def get_active_users_info() -> Dict:
        """获取活跃用户信息"""
        sessions = UserSession.objects.all()
        return {
            "total_users": sessions.count(),
            "users": [session.to_dict() for session in sessions]
        }
    
    @staticmethod
    @database_sync_to_async
    def get_user_count() -> int:
        """获取活跃用户数量"""
        return UserSession.objects.count()

# 全局会话管理器实例
session_manager = SessionManager() 