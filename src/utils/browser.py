"""
浏览器管理器
============
负责 ChromiumPage 的生命周期管理，支持有头/无头模式切换
"""

from DrissionPage import ChromiumPage, ChromiumOptions
import time

class BrowserManager:
    """管理 DrissionPage 实例"""
    
    def __init__(self):
        self.page = None
        self._is_headless = False
        
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
        
        co = ChromiumOptions()
        # 优化配置
        co.set_argument('--no-sandbox')
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
            self.page.set.window.max() if not headless else None
            
            return self.page
        except Exception as e:
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
