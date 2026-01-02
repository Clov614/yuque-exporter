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

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
        
        # 初始化 Session 并配置重试策略
        self.session = requests.Session()
        retries = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504, 429],
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
    
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
        max_retries: int = 120
    ) -> Optional[str]:
        """导出文档，返回下载链接"""
        url = self.API_DOC_EXPORT.format(doc_id=doc.id)
        
        options_str = ""
        if export_type == ExportType.MARKDOWN:
            options_str = json.dumps({"latexType": 1, "useMdai": 1})
        elif export_type == ExportType.PDF:
            options_str = json.dumps({"enableToc": 1})

        payload = {
            "type": export_type.value,
            "force": 0,
            "options": options_str
        }
        
        try:
            # 1. 发起导出请求
            response = self._request_api("POST", url, json=payload)
            
            # 特殊处理：未发布文档
            if response and response.get('status') == 400:
                msg = response.get('message', '')
                if "请发布后再导出" in msg:
                    print(f"⚠️ 文档未发布: {doc.title}，将创建空文件")
                    return "EMPTY_DOC"
            
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

    def download_file(
        self, 
        url: str, 
        save_path: str, 
        progress_callback: Optional[Any] = None
    ) -> bool:
        """
        下载文件
        
        Args:
            url: 下载链接
            save_path: 保存路径
            progress_callback: 进度回调 (chunk_size, total_size)
        """
        try:
            # 方案二：使用 requests 下载 (更稳定，易于控制进度和验证完整性)
            browser_cookies = self.tab.cookies()
            cookies = {c['name']: c['value'] for c in browser_cookies if 'name' in c and 'value' in c}
            
            headers = {
                "User-Agent": self.tab.user_agent,
                "Referer": "https://www.yuque.com/"
            }
            
            response = self.session.get(url, cookies=cookies, headers=headers, stream=True, timeout=60)
            if response.status_code != 200:
                print(f"❌ 下载请求失败: {response.status_code}")
                return False
            
            total_size = int(response.headers.get('content-length', 0))
            if progress_callback and total_size > 0:
                progress_callback(0, total_size)
            
            from pathlib import Path
            path_obj = Path(save_path)
            
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        if progress_callback:
                            progress_callback(len(chunk), None)
            
            # 验证大小
            if path_obj.exists() and path_obj.stat().st_size > 0:
                return True
            else:
                print("❌ 下载文件为空")
                if path_obj.exists():
                    path_obj.unlink() # 删除空文件
                return False
            
        except Exception as e:
            print(f"❌ 下载异常: {e}")
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
            
            response = self.session.request(
                method, 
                url, 
                cookies=cookies, 
                headers=headers, 
                timeout=30, # 增加默认超时
                **kwargs
            )
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 400:
                # 尝试解析错误信息
                try:
                    return response.json()
                except:
                    pass
                print(f"API Error 400: {response.text[:100]}")
                return None
            else:
                print(f"API Error {response.status_code}: {response.text[:100]}")
                return None
                
        except Exception as e:
            print(f"Request Exception: {e}")
            return None
