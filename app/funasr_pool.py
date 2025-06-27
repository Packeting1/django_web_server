"""
FunASR连接池管理器
优化资源使用，支持连接复用和负载均衡
"""

import time
import asyncio
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass
from .funasr_client import FunASRClient
from .utils import get_system_config_async

logger = logging.getLogger(__name__)

@dataclass
class PooledConnection:
    """池化连接信息"""
    client: FunASRClient
    created_at: float
    last_used: float
    in_use: bool = False
    user_id: Optional[str] = None

class FunASRConnectionPool:
    """FunASR连接池管理器"""
    
    def __init__(self, min_connections: int = 2, max_connections: int = 10, max_idle_time: int = 300):
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.max_idle_time = max_idle_time  # 最大空闲时间（秒）
        
        self.connections: List[PooledConnection] = []
        self.user_connections: Dict[str, PooledConnection] = {}  # 用户到连接的映射
        self._lock = asyncio.Lock()
        self._cleanup_task = None
        self._initialized = False
    
    async def initialize(self):
        """初始化连接池"""
        if self._initialized:
            return
        
        async with self._lock:
            if self._initialized:
                return
            
            logger.info(f"初始化FunASR连接池，最小连接数: {self.min_connections}")
            
            # 创建最小连接数
            for i in range(self.min_connections):
                try:
                    conn = await self._create_connection()
                    self.connections.append(conn)
                    logger.info(f"创建连接池连接 {i+1}/{self.min_connections}")
                except Exception as e:
                    logger.error(f"创建连接池连接失败: {e}")
            
            # 启动清理任务
            self._cleanup_task = asyncio.create_task(self._cleanup_idle_connections())
            self._initialized = True
            
            logger.info(f"FunASR连接池初始化完成，当前连接数: {len(self.connections)}")
    
    async def get_connection(self, user_id: str) -> Optional[FunASRClient]:
        """为用户获取连接"""
        async with self._lock:
            # 检查用户是否已有连接
            if user_id in self.user_connections:
                conn = self.user_connections[user_id]
                if conn.client.is_connected():
                    conn.last_used = time.time()
                    return conn.client
                else:
                    # 连接已断开，移除映射
                    await self._remove_user_connection(user_id)
            
            # 寻找空闲连接
            for conn in self.connections:
                if not conn.in_use and conn.client.is_connected():
                    conn.in_use = True
                    conn.user_id = user_id
                    conn.last_used = time.time()
                    self.user_connections[user_id] = conn
                    logger.info(f"为用户 {user_id} 分配连接池连接")
                    return conn.client
            
            # 如果没有空闲连接且未达到最大连接数，创建新连接
            if len(self.connections) < self.max_connections:
                try:
                    conn = await self._create_connection()
                    conn.in_use = True
                    conn.user_id = user_id
                    conn.last_used = time.time()
                    self.connections.append(conn)
                    self.user_connections[user_id] = conn
                    logger.info(f"为用户 {user_id} 创建新连接池连接")
                    return conn.client
                except Exception as e:
                    logger.error(f"创建新连接失败: {e}")
            
            # 连接池已满，返回None
            logger.warning(f"连接池已满，无法为用户 {user_id} 分配连接")
            return None
    
    async def release_connection(self, user_id: str):
        """释放用户连接"""
        async with self._lock:
            await self._remove_user_connection(user_id)
    
    async def _remove_user_connection(self, user_id: str):
        """移除用户连接映射"""
        if user_id in self.user_connections:
            conn = self.user_connections[user_id]
            conn.in_use = False
            conn.user_id = None
            conn.last_used = time.time()
            del self.user_connections[user_id]
            logger.info(f"释放用户 {user_id} 的连接")
    
    async def _create_connection(self) -> PooledConnection:
        """创建新连接"""
        client = FunASRClient()
        await client.connect()
        
        # 发送初始配置
        from .funasr_client import create_stream_config_async
        config = await create_stream_config_async()
        await client.send_config(config)
        
        return PooledConnection(
            client=client,
            created_at=time.time(),
            last_used=time.time()
        )
    
    async def _cleanup_idle_connections(self):
        """清理空闲连接的后台任务"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次
                
                async with self._lock:
                    current_time = time.time()
                    connections_to_remove = []
                    
                    for i, conn in enumerate(self.connections):
                        # 保留最小连接数，清理超时的空闲连接
                        if (not conn.in_use and 
                            len(self.connections) > self.min_connections and
                            current_time - conn.last_used > self.max_idle_time):
                            connections_to_remove.append(i)
                    
                    # 从后往前删除，避免索引问题
                    for i in reversed(connections_to_remove):
                        conn = self.connections[i]
                        try:
                            await conn.client.disconnect()
                        except Exception as e:
                            logger.error(f"关闭连接失败: {e}")
                        
                        self.connections.pop(i)
                        logger.info("清理1个空闲连接")
                    
                    if connections_to_remove:
                        logger.info(f"连接池清理完成，当前连接数: {len(self.connections)}")
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"连接池清理任务错误: {e}")
    
    async def close(self):
        """关闭连接池"""
        logger.info("关闭FunASR连接池...")
        
        # 停止清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # 关闭所有连接
        async with self._lock:
            for conn in self.connections:
                try:
                    await conn.client.disconnect()
                except Exception as e:
                    logger.error(f"关闭连接失败: {e}")
            
            self.connections.clear()
            self.user_connections.clear()
        
        logger.info("FunASR连接池已关闭")
    
    def get_stats(self) -> Dict:
        """获取连接池统计信息"""
        total_connections = len(self.connections)
        active_connections = sum(1 for conn in self.connections if conn.in_use)
        idle_connections = total_connections - active_connections
        
        return {
            "total_connections": total_connections,
            "active_connections": active_connections,
            "idle_connections": idle_connections,
            "active_users": len(self.user_connections),
            "max_connections": self.max_connections,
            "min_connections": self.min_connections
        }

# 全局连接池实例
_connection_pool: Optional[FunASRConnectionPool] = None

async def get_connection_pool() -> FunASRConnectionPool:
    """获取全局连接池实例"""
    global _connection_pool
    if _connection_pool is None:
        # 从数据库配置获取连接池参数
        config = await get_system_config_async()
        _connection_pool = FunASRConnectionPool(
            min_connections=config.pool_min_connections,
            max_connections=config.pool_max_connections,
            max_idle_time=config.pool_max_idle_time
        )
        await _connection_pool.initialize()
    return _connection_pool

async def close_connection_pool():
    """关闭全局连接池"""
    global _connection_pool
    if _connection_pool:
        await _connection_pool.close()
        _connection_pool = None 