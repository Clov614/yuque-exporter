"""
语雀凭证持久化管理
===================
负责保存和恢复浏览器 cookies，实现免重复登录
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum, auto

class LoginStatus(Enum):
    """登录状态枚举"""
    LOGGED_IN = auto()  # 已登录且会话有效
    EXPIRED = auto()    # 曾登录但会话失效
    NONE = auto()       # 未登录/无凭证

class YuqueAuth:
    """
    语雀凭证管理器
    """
    
    # 凭证存储路径
    CREDENTIALS_DIR = Path.home() / ".yuque"
    COOKIES_FILE = CREDENTIALS_DIR / "cookies.json"
    
    # 语雀关键 URL
    DASHBOARD_URL = "https://www.yuque.com/dashboard"
    
    def __init__(self):
        """初始化，确保存储目录存在"""
        self.CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    
    def save_cookies(self, tab) -> bool:
        """从浏览器保存 cookies 到本地文件"""
        try:
            cookies = tab.cookies()
            data = {
                "saved_at": datetime.now().isoformat(),
                "cookies": cookies
            }
            
            with open(self.COOKIES_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            return True
        except Exception as e:
            print(f"❌ 保存 Cookies 失败: {e}")
            return False
    
    def load_cookies(self, tab) -> bool:
        """从本地文件恢复 cookies 到浏览器"""
        if not self.COOKIES_FILE.exists():
            return False
        
        try:
            with open(self.COOKIES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            cookies = data.get("cookies", [])
            if not cookies:
                return False
            
            # 先访问语雀主页以设置域名上下文 (DrissionPage 要求)
            tab.get("https://www.yuque.com")
            
            # 注入 cookies
            tab.set.cookies(cookies)
            return True
            
        except Exception as e:
            print(f"❌ 加载 Cookies 失败: {e}")
            return False
    
    def check_login_status(self, tab) -> LoginStatus:
        """
        检查登录状态
        
        Returns:
            LoginStatus: 登录状态
        """
        try:
            # 1. 尝试加载 cookies
            if not self.load_cookies(tab):
                return LoginStatus.NONE
            
            # 2. 验证会话
            # 访问 dashboard（需要登录才能访问）
            tab.get(self.DASHBOARD_URL)
            tab.wait.load_start()
            
            current_url = tab.url
            if "login" in current_url.lower():
                return LoginStatus.EXPIRED
            
            if "dashboard" in current_url.lower():
                return LoginStatus.LOGGED_IN
            
            # 备选：检查特定元素
            user_ele = tab.ele('@@tag()=img@@class:avatar', timeout=3)
            if user_ele:
                return LoginStatus.LOGGED_IN
                
            return LoginStatus.EXPIRED
            
        except Exception as e:
            print(f"❌ 检查登录状态出错: {e}")
            return LoginStatus.NONE
    
    def clear_credentials(self) -> bool:
        """清除已保存的凭证"""
        try:
            if self.COOKIES_FILE.exists():
                self.COOKIES_FILE.unlink()
            return True
        except Exception as e:
            print(f"❌ 清除凭证失败: {e}")
            return False
