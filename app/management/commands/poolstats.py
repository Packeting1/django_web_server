"""
查看FunASR连接池状态的管理命令
"""

from django.core.management.base import BaseCommand
from asgiref.sync import async_to_sync
from app.funasr_pool import get_connection_pool
from app.models import SystemConfig

class Command(BaseCommand):
    help = '查看FunASR连接池状态'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--watch',
            action='store_true',
            help='持续监控连接池状态（每5秒刷新一次）',
        )
    
    def handle(self, *args, **options):
        if options['watch']:
            import time
            self.stdout.write(self.style.SUCCESS('开始监控连接池状态（按 Ctrl+C 退出）...'))
            try:
                while True:
                    self.show_stats()
                    time.sleep(5)
            except KeyboardInterrupt:
                self.stdout.write(self.style.SUCCESS('\n监控已停止'))
        else:
            self.show_stats()
    
    def show_stats(self):
        """显示连接池状态"""
        try:
            # 获取配置
            config = SystemConfig.get_config()
            
            self.stdout.write("=" * 60)
            self.stdout.write(f"FunASR连接池状态 ({config.updated_at.strftime('%Y-%m-%d %H:%M:%S')})")
            self.stdout.write("=" * 60)
            
            # 显示配置信息
            self.stdout.write(f"连接池模式: {'开启' if config.use_connection_pool else '关闭（独立连接模式）'}")
            if config.use_connection_pool:
                self.stdout.write(f"最小连接数: {config.pool_min_connections}")
                self.stdout.write(f"最大连接数: {config.pool_max_connections}")
                self.stdout.write(f"最大空闲时间: {config.pool_max_idle_time}秒")
                
                # 获取运行时状态
                async def get_pool_stats():
                    try:
                        pool = await get_connection_pool()
                        return pool.get_stats()
                    except Exception as e:
                        return {"error": str(e)}
                
                stats = async_to_sync(get_pool_stats)()
                
                if "error" in stats:
                    self.stdout.write(self.style.ERROR(f"获取连接池状态失败: {stats['error']}"))
                else:
                    self.stdout.write("-" * 40)
                    self.stdout.write("运行时状态:")
                    self.stdout.write(f"总连接数: {stats['total_connections']}")
                    self.stdout.write(f"活跃连接数: {stats['active_connections']}")
                    self.stdout.write(f"空闲连接数: {stats['idle_connections']}")
                    self.stdout.write(f"活跃用户数: {stats['active_users']}")
                    
                    # 计算使用率
                    if stats['total_connections'] > 0:
                        usage_rate = (stats['active_connections'] / stats['total_connections']) * 100
                        self.stdout.write(f"连接使用率: {usage_rate:.1f}%")
                        
                        if usage_rate > 80:
                            self.stdout.write(self.style.WARNING("⚠️  连接使用率较高，建议增加最大连接数"))
                        elif usage_rate < 20 and stats['total_connections'] > config.pool_min_connections:
                            self.stdout.write(self.style.SUCCESS("✅ 连接池资源充足"))
                    
                    # 状态指示
                    if stats['total_connections'] >= config.pool_max_connections:
                        self.stdout.write(self.style.WARNING("🔴 连接池已达到最大连接数"))
                    elif stats['total_connections'] <= config.pool_min_connections:
                        self.stdout.write(self.style.SUCCESS("🟢 连接池处于最小连接状态"))
                    else:
                        self.stdout.write(self.style.SUCCESS("🟡 连接池处于动态调整状态"))
            else:
                self.stdout.write("当前使用独立连接模式，每个用户一个独立连接")
            
            self.stdout.write("-" * 40)
            self.stdout.write(f"FunASR服务器: {config.get_funasr_uri()}")
            self.stdout.write(f"SSL: {'启用' if config.funasr_ssl else '禁用'}")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"获取状态失败: {e}"))
            import traceback
            self.stdout.write(traceback.format_exc()) 