"""
浏览器管理器
============
负责 ChromiumPage 的生命周期管理，支持有头/无头模式切换
"""

import contextlib
from pathlib import Path
from typing import Optional, Any

from DrissionPage import ChromiumPage, ChromiumOptions

class BrowserManager:
    """管理 DrissionPage 实例"""
    
    def __init__(self, user_data_dir: Optional[Path] = None, lifecycle_lock: Any = None):
        self.page = None
        self._is_headless = False
        self._lifecycle_lock = lifecycle_lock
        self._lock_context = None
        self.user_data_dir = (
            Path(user_data_dir).expanduser().resolve()
            if user_data_dir is not None
            else None
        )
        
    def start(self, headless: bool = True) -> ChromiumPage:
        """
        启动浏览器
        Args:
            headless: 是否无头模式
        """
        # 如果已经启动且模式相同，直接返回
        if self.page and self._is_headless == headless:
            try:
                # 检查页面是否存活
                if self.page.url: 
                    return self.page
            except:
                pass # 页面可能已关闭

        # 如果需要切换模式或尚未启动，先关闭旧的
        self.quit()
        if self._lifecycle_lock is not None:
            self._lock_context = self._lifecycle_lock()
            self._lock_context.__enter__()

        co = ChromiumOptions()
        if self.user_data_dir is not None:
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            co.set_user_data_path(str(self.user_data_dir))
        # 始终使用空闲端口，避免与用户桌面 Chrome（默认 9222）冲突；
        # 桌面 Chrome 开着时也能正常启动独立的导出浏览器。
        co.auto_port(True)
        # 优化配置（保留 Chromium 默认 sandbox）
        co.set_argument('--disable-gpu')
        co.mute(True) # 静音
        
        if headless:
            co.headless(True)
        else:
            co.headless(False)
            
        try:
            self.page = ChromiumPage(co)
            self._is_headless = headless

            # 设置一些基础属性
            if headless:
                # 无头默认视口过小会导致下拉菜单/弹窗在视口外点不动，
                # 这里对齐有头最大化的效果。
                try:
                    self.page.set.window.size(1920, 1080)
                except Exception:
                    pass
            else:
                self.page.set.window.max()

            return self.page
        except Exception as e:
            if self._lock_context is not None:
                self._lock_context.__exit__(type(e), e, e.__traceback__)
                self._lock_context = None
            print(f"❌ 启动浏览器失败: {e}")
            raise
            
    def restart_headed(self) -> ChromiumPage:
        """重启为有头模式 (用于登录/验证码)"""
        print("🔄 正在切换到可视化模式...")
        return self.start(headless=False)
        
    def restart_headless(self) -> ChromiumPage:
        """重启为无头模式 (用于后台任务)"""
        print("🔄 正在切换到后台模式...")
        return self.start(headless=True)
        
    def quit(self):
        """关闭浏览器"""
        if self.page:
            try:
                self.page.quit()
            except:
                pass
            self.page = None
            if self._lock_context is not None:
                self._lock_context.__exit__(None, None, None)
                self._lock_context = None
