"""
语雀 API 客户端
===============
封装与语雀的所有交互逻辑
"""

import json
import time
import requests
from enum import Enum
from typing import List, Optional, Any, Dict
from .auth import YuqueAuth, LoginStatus
from .models import Repository, Document

class ExportType(Enum):
    """文档导出格式"""
    MARKDOWN = "markdown"
    WORD = "word"
    PDF = "pdf"
    LAKEBOOK = "lakebook" # Added based on common Yuque usage, though original only had 3

class YuqueClient:
    """
    语雀客户端 - 基于 DrissionPage
    """
    
    BASE_URL = "https://www.yuque.com"
    API_COMMON_USED = "https://www.yuque.com/api/mine/common_used"
    API_DOC_EXPORT = "https://www.yuque.com/api/docs/{doc_id}/export"
    
    def __init__(self, tab):
        """
        Args:
            tab: DrissionPage 对象 (ChromiumPage or SessionPage)
        """
        self.tab = tab
    
    def get_repositories(self) -> List[Repository]:
        """获取所有知识库"""
        print("📚 获取知识库列表...")
        try:
            # 使用 requests 发送请求，因为 DrissionPage 直接 get 可能返回 HTML 渲染后的内容，
            # 而我们想要纯 JSON。虽然 DP 也可以获取源码，但他会自动处理 JSON 吗？
            # 沿用 requests 方案更稳健
            data = self._request_api("GET", self.API_COMMON_USED)
            if not data:
                return []
            
            books = data.get('data', {}).get('books', [])
            return [Repository.from_api_response(book) for book in books]
            
        except Exception as e:
            print(f"❌ 获取知识库列表失败: {e}")
            return []

    def get_catalog_nodes(self, repo: Repository) -> List[Document]:
        """获取知识库目录结构"""
        url = "https://www.yuque.com/api/catalog_nodes"
        params = {"book_id": repo.id, "format": "list"}
        
        try:
            data = self._request_api("GET", url, params=params)
            if not data:
                return []
            
            nodes_data = data.get('data', [])
            nodes = []
            for item in nodes_data:
                item['book_id'] = repo.id
                nodes.append(Document.from_api_response(item))
            return nodes
            
        except Exception as e:
            print(f"❌ 获取目录失败: {e}")
            return []

    def export_document(
        self, 
        doc: Document, 
        export_type: ExportType = ExportType.MARKDOWN,
        max_retries: int = 20
    ) -> Optional[str]:
        """导出文档，返回下载链接"""
        url = self.API_DOC_EXPORT.format(doc_id=doc.id)
        
        payload = {
            "type": export_type.value,
            "force": 0,
            "options": json.dumps({"latexType": 1, "useMdai": 1}) if export_type == ExportType.MARKDOWN else ""
        }
        
        try:
            # 1. 发起导出请求
            response = self._request_api("POST", url, json=payload)
            if not response:
                return None
            
            data = response.get('data', {})
            state = data.get('state', '')
            
            # 2. 轮询状态
            retry_count = 0
            while state == 'pending' and retry_count < max_retries:
                time.sleep(1.5)
                response = self._request_api("POST", url, json=payload)
                if response:
                    data = response.get('data', {})
                    state = data.get('state', '')
                retry_count += 1
            
            if state != 'success':
                print(f"❌ 导出超时或失败: state={state}")
                return None
                
            download_url = data.get('url', '')
            if download_url.startswith('/'):
                download_url = f"{self.BASE_URL}{download_url}"
                
            return download_url
            
        except Exception as e:
            print(f"❌ 导出文档异常: {e}")
            return None

    def download_file(self, url: str, save_path: str) -> bool:
        """下载文件"""
        try:
            # 使用 DrissionPage 下载，利用其优秀的下载管理
            from pathlib import Path
            path_obj = Path(save_path)
            self.tab.download(url, save_path=str(path_obj.parent), rename=path_obj.name)
            
            # 等待文件出现 (简单超时机制)
            for _ in range(60): 
                if path_obj.exists():
                    return True
                time.sleep(1)
            return False
        except Exception as e:
            print(f"❌ 下载失败: {e}")
            return False

    def _request_api(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        """通用 API 请求封装 (使用 requests + browser cookies)"""
        try:
            # 从浏览器获取 cookie
            browser_cookies = self.tab.cookies()
            cookies = {c['name']: c['value'] for c in browser_cookies if 'name' in c and 'value' in c}
            
            headers = {
                "User-Agent": self.tab.user_agent,
                "Referer": "https://www.yuque.com/",
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest" 
            }
            
            # 合并自定义 headers
            if 'headers' in kwargs:
                headers.update(kwargs.pop('headers'))
            
            response = requests.request(
                method, 
                url, 
                cookies=cookies, 
                headers=headers, 
                **kwargs
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"API Error {response.status_code}: {response.text[:100]}")
                return None
                
        except Exception as e:
            print(f"Request Exception: {e}")
            return None
