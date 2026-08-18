"""
语雀 API 客户端
===============
封装与语雀的所有交互逻辑
"""

import ipaddress
import json
import os
import socket
import tempfile
import time
from pathlib import Path
from enum import Enum
from typing import List, Optional, Any, Dict
from urllib.parse import urljoin, urlparse

import requests
from .auth import YuqueAuth, LoginStatus
from .models import Repository, Document

class ExportType(Enum):
    """文档导出格式"""
    MARKDOWN = "markdown"
    WORD = "word"
    PDF = "pdf"
    LAKEBOOK = "lake" # Fixed: API requires "lake" instead of "lakebook"

from requests.adapters import HTTPAdapter
from urllib3 import PoolManager
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.poolmanager import PoolKey, _default_key_normalizer
from urllib3.util import connection as urllib3_connection
from urllib3.util.retry import Retry


class _PinnedHTTPConnection(HTTPConnection):
    def __init__(self, *args, pinned_ip: str, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        return urllib3_connection.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
            source_address=self.source_address,
            socket_options=self.socket_options,
        )


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, pinned_ip: str, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        return urllib3_connection.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
            source_address=self.source_address,
            socket_options=self.socket_options,
        )


class _PinnedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


def _pinned_pool_key(request_context):
    context = dict(request_context)
    context.pop("pinned_ip", None)
    return _default_key_normalizer(PoolKey, context)


class _PinnedHTTPAdapter(HTTPAdapter):
    def __init__(self, pinned_ip: str):
        self.pinned_ip = pinned_ip
        super().__init__(max_retries=0)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["pinned_ip"] = self.pinned_ip
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )
        self.poolmanager.pool_classes_by_scheme = {
            "http": _PinnedHTTPConnectionPool,
            "https": _PinnedHTTPSConnectionPool,
        }
        self.poolmanager.key_fn_by_scheme = {
            "http": _pinned_pool_key,
            "https": _pinned_pool_key,
        }


class YuqueClient:
    """
    语雀客户端 - 基于 DrissionPage
    """
    
    BASE_URL = "https://www.yuque.com"
    API_COMMON_USED = "https://www.yuque.com/api/mine/common_used"
    API_DOC_EXPORT = "https://www.yuque.com/api/docs/{doc_id}/export"
    DEFAULT_IMAGE_MAX_BYTES = 20 * 1024 * 1024
    MAX_IMAGE_DOWNLOAD_SECONDS = 120
    MAX_IMAGE_REDIRECTS = 3
    
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

        # 外部图片使用独立连接池，避免 Session cookie jar 泄露语雀会话。
        self.external_session = requests.Session()
        self.external_session.trust_env = False
        external_adapter = HTTPAdapter(max_retries=retries)
        self.external_session.mount("https://", external_adapter)
        self.external_session.mount("http://", external_adapter)

        self.auth = YuqueAuth()

    def login(self) -> bool:
        """
        执行登录流程 (需在有头模式下调用)
        """
        print("请在浏览器中完成登录...")
        
        # 确保环境纯净：清除 Cookies 和 缓存
        try:
            # 使用 CDP 命令强力清除
            self.tab.run_cdp("Network.clearBrowserCookies")
            self.tab.run_cdp("Network.clearBrowserCache")
        except Exception as e:
            print(f"⚠️ 清理浏览器数据失败: {e}")

        self.tab.get("https://www.yuque.com/login")
        
        # 等待登录成功 (轮询检查 URL 或 元素)
        # 语雀登录成功后通常会跳转到 /dashboard 或 个人主页
        max_wait = 300 # 5分钟
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            time.sleep(1)
            try:
                curr_url = self.tab.url
                if "dashboard" in curr_url or "yuque.com/u/" in curr_url:
                    # 登录成功
                    # 额外等待一下确保 cookie 写入
                    time.sleep(2)
                    if self.auth.save_cookies(self.tab):
                         return True
            except:
                pass
                
        print("❌ 登录超时")
        return False
    
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

    def download_external_image(
        self,
        url: str,
        save_path: str | Path,
        *,
        max_bytes: int = DEFAULT_IMAGE_MAX_BYTES,
    ) -> bool:
        """Download a public image without sending Yuque authentication cookies."""
        if max_bytes <= 0:
            return False

        response = None
        current_url = url
        deadline = time.monotonic() + self.MAX_IMAGE_DOWNLOAD_SECONDS
        try:
            for _ in range(self.MAX_IMAGE_REDIRECTS + 1):
                if time.monotonic() >= deadline:
                    return False
                response = self._request_external_image(current_url, deadline)
                if response is None:
                    return False
                if not 300 <= response.status_code < 400:
                    break
                location = response.headers.get("Location") or response.headers.get("location")
                if not location:
                    return False
                current_url = urljoin(current_url, location)
                response.close() if hasattr(response, "close") else None
                response = None
            else:
                return False

            return self._save_external_response(
                response, Path(save_path), max_bytes, deadline
            )
        except (OSError, requests.RequestException, socket.error, ValueError):
            return False
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()

    def _request_external_image(self, url: str, deadline: float) -> Any:
        pinned_ip = self._public_ip_for_url(url)
        remaining_seconds = deadline - time.monotonic()
        if pinned_ip is None or remaining_seconds <= 0:
            return None
        headers = {"User-Agent": getattr(self.tab, "user_agent", "YuqueExporter/1.0")}
        timeout_seconds = max(1.0, min(10.0, remaining_seconds))
        return self._external_get(url, pinned_ip, headers, timeout_seconds)

    @staticmethod
    def _save_external_response(
        response: Any,
        destination: Path,
        max_bytes: int,
        deadline: float,
    ) -> bool:
        if response is None or not 200 <= response.status_code < 300:
            return False

        content_type = (
            response.headers.get("Content-Type")
            or response.headers.get("content-type")
            or ""
        ).split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            return False

        content_length = response.headers.get("Content-Length") or response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    return False
            except ValueError:
                return False

        temporary_path: Optional[Path] = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".part",
                delete=False,
            ) as temp_file:
                temporary_path = Path(temp_file.name)
                total_size = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if time.monotonic() >= deadline:
                        return False
                    if not chunk:
                        continue
                    total_size += len(chunk)
                    if total_size > max_bytes:
                        return False
                    temp_file.write(chunk)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            if total_size == 0:
                return False
            os.replace(temporary_path, destination)
            temporary_path = None
            return True
        finally:
            if temporary_path and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _public_ip_for_url(url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            scheme = parsed.scheme.lower()
            if scheme not in {"http", "https"}:
                return None
            if parsed.username is not None or parsed.password is not None:
                return None
            if not parsed.hostname:
                return None

            try:
                addresses = [ipaddress.ip_address(parsed.hostname)]
            except ValueError:
                port = parsed.port or (443 if scheme == "https" else 80)
                rows = socket.getaddrinfo(
                    parsed.hostname,
                    port,
                    type=socket.SOCK_STREAM,
                )
                addresses = [ipaddress.ip_address(row[4][0]) for row in rows]

            if not addresses or not all(address.is_global for address in addresses):
                return None
            return str(addresses[0])
        except (OSError, ValueError, UnicodeError):
            return None

    @classmethod
    def _is_safe_external_url(cls, url: str) -> bool:
        return cls._public_ip_for_url(url) is not None

    def _external_get(
        self,
        url: str,
        pinned_ip: str,
        headers: Dict[str, str],
        timeout_seconds: float,
    ) -> Any:
        """Request a URL through a connection pinned to the validated IP."""
        if not isinstance(self.external_session, requests.Session):
            return self.external_session.get(
                url,
                headers=headers,
                cookies={},
                stream=True,
                timeout=(timeout_seconds, timeout_seconds),
                allow_redirects=False,
            )

        adapter = _PinnedHTTPAdapter(pinned_ip)
        self.external_session.mount("https://", adapter)
        self.external_session.mount("http://", adapter)
        return self.external_session.get(
            url,
            headers=headers,
            cookies={},
            stream=True,
            timeout=60,
            allow_redirects=False,
        )

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
