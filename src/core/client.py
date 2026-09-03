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
from .auth import YuqueAuth, LoginStatus, is_authenticated_yuque_url
from .models import Repository, Document
from .repository_reference import RepositoryReference
from .repository_resolver import (
    RepositoryAccessDeniedError,
    RepositoryAuthenticationError,
    RepositoryHttpResult,
    RepositoryNotFoundError,
    RepositoryResolutionError,
    RepositoryResolver,
    RepositoryResponseError,
    RepositoryTransportError,
)
from .download_support import ExportDownloadMixin
from .favorite_repository_provider import FavoriteRepositoryProvider

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


class YuqueClient(ExportDownloadMixin):
    """
    语雀客户端 - 基于 DrissionPage
    """
    
    BASE_URL = "https://www.yuque.com"
    API_COMMON_USED = "https://www.yuque.com/api/mine/common_used"
    FAVORITES_PAGE = "https://www.yuque.com/dashboard/collections"
    API_DOC_EXPORT = "https://www.yuque.com/api/docs/{doc_id}/export"
    API_DOC_CREATE = "https://www.yuque.com/api/docs"
    DEFAULT_IMAGE_MAX_BYTES = 20 * 1024 * 1024
    DEFAULT_EXPORT_MAX_BYTES = 2 * 1024 * 1024 * 1024
    MAX_IMAGE_DOWNLOAD_SECONDS = 120
    MAX_EXPORT_DOWNLOAD_SECONDS = 30 * 60
    MAX_IMAGE_REDIRECTS = 3
    TUN_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
    TRUSTED_TUN_IMAGE_DOMAINS = (
        "nlark.com",
        "alipay.com",
        "alipayobjects.com",
        "yuque.com",
    )
    TRUSTED_TUN_IMAGE_EXACT_DOMAINS = (
        "yuque.antfin.com",
        "lark-assets-prod-aliyun.oss-cn-hangzhou.aliyuncs.com",
    )
    YUQUE_UNSCOPED_COOKIE_NAMES = frozenset({"_yuque_session", "yuque_ctoken"})
    
    def __init__(self, tab, auth: Optional[YuqueAuth] = None):
        """
        Args:
            tab: DrissionPage 对象 (ChromiumPage or SessionPage)
            auth: 可选的显式凭据存储，用于隔离 CLI profile。
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

        self.auth = auth or YuqueAuth()

    def login(self) -> bool:
        """
        执行登录流程 (需在有头模式下调用)
        """
        print("请在浏览器中完成登录...")
        
        # 确保环境纯净：清除 Cookies 和 缓存
        try:
            self.tab.run_cdp("Network.clearBrowserCookies")
            self.tab.run_cdp("Network.clearBrowserCache")
        except Exception:
            print("❌ 无法安全清理旧浏览器会话，登录已取消")
            return False

        self.tab.get("https://www.yuque.com/login")
        
        # 等待登录成功 (轮询检查 URL 或 元素)
        # 语雀登录成功后通常会跳转到 /dashboard 或 个人主页
        max_wait = 300 # 5分钟
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            time.sleep(1)
            try:
                curr_url = self.tab.url
                if is_authenticated_yuque_url(curr_url):
                    # 登录成功
                    # 额外等待一下确保 cookie 写入
                    time.sleep(2)
                    if self.auth.save_cookies(self.tab):
                         return True
            except:
                pass
                
        print("❌ 登录超时")
        return False
    
    def get_repository(
        self,
        reference: RepositoryReference | int | str,
    ) -> Repository:
        """Resolve one repository without depending on a repository listing."""
        parsed_reference = (
            reference
            if isinstance(reference, RepositoryReference)
            else RepositoryReference.parse(reference)
        )
        return RepositoryResolver(self._request_repository).resolve(parsed_reference)

    def get_favorite_repositories(self) -> List[Repository]:
        """获取收藏页中明确标记为 Book 的知识库。"""
        provider = FavoriteRepositoryProvider(
            self._request_favorite_resource,
            self.FAVORITES_PAGE,
        )
        return provider.list_repositories()

    def get_repositories(self) -> List[Repository]:
        """获取常用知识库列表。"""
        print("📚 获取知识库列表...")
        result = self._request_json("GET", self.API_COMMON_USED)
        RepositoryResolver.raise_for_status(result.status_code)
        data = self._require_dict_payload(result.payload, "repository list")
        container = data.get("data", {})
        if not isinstance(container, dict):
            raise RepositoryResponseError("Yuque returned invalid repository list data")
        books = container.get("books", [])
        if not isinstance(books, list):
            raise RepositoryResponseError("Yuque returned invalid repository list items")
        return [RepositoryResolver.repository_from_payload(book) for book in books]

    def get_catalog_nodes(self, repo: Repository) -> List[Document]:
        """获取知识库目录结构。"""
        url = "https://www.yuque.com/api/catalog_nodes"
        params = {"book_id": repo.id, "format": "list"}
        result = self._request_json("GET", url, params=params)
        RepositoryResolver.raise_for_status(result.status_code)
        data = self._require_dict_payload(result.payload, "repository catalog")
        nodes_data = data.get("data", [])
        if not isinstance(nodes_data, list):
            raise RepositoryResponseError("Yuque returned invalid repository catalog data")

        if len(nodes_data) > 10000:
            raise RepositoryResponseError("Yuque returned too many catalog nodes")
        nodes = [
            self._document_from_catalog_item(raw_item, repo.id)
            for raw_item in nodes_data
        ]
        self._validate_catalog_graph(nodes)
        return nodes

    def get_document_updated_at(self, doc: Document) -> Optional[str]:
        """获取文档的服务端更新时间，用于增量导出对比。

        catalog 接口不返回可信的 updated_at，因此增量判断必须走
        单文档 detail 接口。任何失败（网络/鉴权/4xx/非法载荷）
        一律返回 None，调用方按“视为已修改，重导”处理，绝不中断导出。
        """
        from urllib.parse import quote

        candidates = []
        if isinstance(doc.id, int) and doc.id > 0:
            candidates.append(str(doc.id))
        if isinstance(doc.slug, str) and doc.slug:
            slug = quote(doc.slug.strip("/"), safe="")
            if slug and slug not in candidates:
                candidates.append(slug)
        for identifier in candidates:
            timestamp = self._fetch_document_updated_at(identifier, doc.book_id)
            if timestamp:
                return timestamp
        return None

    def _fetch_document_updated_at(self, identifier: str, book_id: int) -> Optional[str]:
        url = f"{self.BASE_URL}/api/docs/{identifier}"
        params = {"book_id": book_id} if book_id else None
        try:
            result = self._request_json("GET", url, params=params)
            RepositoryResolver.raise_for_status(result.status_code)
            if not isinstance(result.payload, dict):
                return None
            data = result.payload.get("data", {})
            if not isinstance(data, dict):
                return None
            updated_at = data.get("updated_at") or data.get("content_updated_at")
            return str(updated_at) if updated_at else None
        except (RepositoryResolutionError, RepositoryTransportError, ValueError, TypeError):
            return None

    def create_markdown_document(
        self,
        repo: Repository,
        title: str,
        body: str,
    ) -> Document:
        """Create one Markdown document via the same protocol the web UI uses.

        Verified against the live frontend bundle (``POST /api/docs`` with
        ``book_id/title/body/type=Doc/format=markdown``) using the user's own
        browser session (cookies + CSRF). Raises RepositoryResolutionError
        subclasses on failure so callers stay fail-closed.
        """
        normalized_title = title.strip()
        if not normalized_title:
            raise RepositoryResponseError("document title cannot be empty")
        payload = {
            "book_id": repo.id,
            "title": normalized_title,
            "body": body,
            "type": "Doc",
            "format": "markdown",
        }
        result = self._request_json("POST", self.API_DOC_CREATE, json=payload)
        if result.status_code == 401:
            raise RepositoryAuthenticationError("Yuque session is not authenticated")
        if result.status_code == 403:
            raise RepositoryAccessDeniedError("Yuque denied document creation")
        if result.status_code == 404:
            raise RepositoryNotFoundError("Yuque repository was not found")
        if result.status_code != 200:
            raise RepositoryTransportError(
                f"Yuque document creation failed with status {result.status_code}"
            )
        if not isinstance(result.payload, dict):
            raise RepositoryResponseError("Yuque returned invalid document JSON")
        data = result.payload.get("data", {})
        if not isinstance(data, dict):
            raise RepositoryResponseError("Yuque returned invalid document data")
        doc_id = data.get("id")
        slug = data.get("slug")
        if not isinstance(doc_id, int) or not isinstance(slug, str) or not slug:
            raise RepositoryResponseError("Yuque did not confirm the created document")
        return Document(
            id=doc_id,
            doc_id=doc_id,
            title=str(data.get("title") or normalized_title),
            slug=slug,
            book_id=repo.id,
        )

    def document_url(self, repo: Repository, doc: Document) -> str:
        """Build the canonical URL for a document in a repository."""
        return f"{self.BASE_URL}/{repo.user_login}/{repo.slug}/{doc.slug}"

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
                literal_address = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                literal_address = None

            if literal_address is not None:
                return str(literal_address) if literal_address.is_global else None

            port = parsed.port or (443 if scheme == "https" else 80)
            rows = socket.getaddrinfo(
                parsed.hostname,
                port,
                type=socket.SOCK_STREAM,
            )
            addresses = [ipaddress.ip_address(row[4][0]) for row in rows]
            if not addresses:
                return None
            if all(address.is_global for address in addresses):
                return str(addresses[0])
            if YuqueClient._is_trusted_tun_fake_ip(parsed.hostname, addresses):
                return str(addresses[0])
            return None
        except (OSError, ValueError, UnicodeError):
            return None

    @classmethod
    def _is_trusted_tun_fake_ip(
        cls,
        hostname: str,
        addresses: List[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> bool:
        normalized_hostname = hostname.rstrip(".").lower()
        trusted_domain = normalized_hostname in cls.TRUSTED_TUN_IMAGE_EXACT_DOMAINS or any(
            normalized_hostname == domain
            or normalized_hostname.endswith(f".{domain}")
            for domain in cls.TRUSTED_TUN_IMAGE_DOMAINS
        )
        return trusted_domain and all(
            isinstance(address, ipaddress.IPv4Address)
            and address in cls.TUN_FAKE_IP_NETWORK
            for address in addresses
        )

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
            timeout=(timeout_seconds, timeout_seconds),
            allow_redirects=False,
        )

    def _request_favorite_resource(self, url: str) -> RepositoryHttpResult:
        """Read favorites HTML/JSON with a strict response-size bound."""
        max_bytes = FavoriteRepositoryProvider.MAX_HTML_BYTES
        cookies = self._yuque_cookies(self.tab.cookies(), url)
        headers = self._api_headers(cookies)
        try:
            response = self.session.request(
                "GET",
                url,
                cookies=cookies,
                headers=headers,
                timeout=30,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            raise RepositoryTransportError("failed to request favorites source") from exc
        content_length = response.headers.get("Content-Length") or response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise RepositoryResponseError("favorites response is too large")
            except ValueError as exc:
                raise RepositoryResponseError("favorites response length is invalid") from exc
        chunks = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise RepositoryResponseError("favorites response is too large")
                chunks.append(chunk)
        except requests.RequestException as exc:
            raise RepositoryTransportError("failed to read favorites source") from exc
        raw = b"".join(chunks)
        content_type = response.headers.get("Content-Type", "")
        encoding = getattr(response, "encoding", None) or "utf-8"
        try:
            text = raw.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError) as exc:
            raise RepositoryResponseError("favorites response encoding is invalid") from exc
        try:
            payload: Any = json.loads(text)
        except (TypeError, ValueError):
            payload = text
        return RepositoryHttpResult(response.status_code, payload, content_type)

    def _request_repository(self, url: str) -> RepositoryHttpResult:
        """Request repository metadata while preserving the HTTP status."""
        return self._request_json("GET", url)

    def _request_json(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> RepositoryHttpResult:
        """Request Yuque JSON while preserving status and filtering cookies."""
        cookies = self._yuque_cookies(self.tab.cookies(), url)
        headers = self._api_headers(cookies)
        custom_headers = kwargs.pop("headers", None)
        if isinstance(custom_headers, dict):
            headers = {**headers, **custom_headers}
        try:
            response = self.session.request(
                method,
                url,
                cookies=cookies,
                headers=headers,
                timeout=30,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RepositoryTransportError("failed to request Yuque API") from exc

        content_type = response.headers.get("Content-Type", "")
        try:
            payload = response.json()
        except (TypeError, ValueError, requests.JSONDecodeError):
            response_text = getattr(response, "text", None)
            payload = response_text if isinstance(response_text, str) else None
        return RepositoryHttpResult(
            status_code=response.status_code,
            payload=payload,
            content_type=content_type,
        )

    @classmethod
    def _yuque_cookies(
        cls,
        browser_cookies: List[Dict[str, Any]],
        request_url: str,
    ) -> Dict[str, str]:
        """Apply browser-like domain/path scoping to Yuque API cookies."""
        parsed = urlparse(request_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme.lower() != "https" or host not in {"yuque.com", "www.yuque.com"}:
            return {}
        request_path = parsed.path or "/"
        candidates = []
        for cookie in browser_cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            raw_domain = str(cookie.get("domain") or "").lower()
            domain = raw_domain.lstrip(".")
            cookie_path = str(cookie.get("path") or "/")
            if not cls._cookie_path_matches(cookie_path, request_path):
                continue
            if domain:
                domain_matches = host == domain or (
                    raw_domain.startswith(".") and host.endswith(f".{domain}")
                )
                if not domain_matches:
                    continue
                specificity = (host == domain, len(cookie_path))
            elif name in cls.YUQUE_UNSCOPED_COOKIE_NAMES:
                specificity = (True, len(cookie_path))
            else:
                continue
            candidates.append((specificity, name, value))

        result: Dict[str, str] = {}
        for _, name, value in sorted(candidates):
            result = {**result, name: value}
        return result

    @staticmethod
    def _cookie_path_matches(cookie_path: str, request_path: str) -> bool:
        if request_path == cookie_path:
            return True
        if not request_path.startswith(cookie_path):
            return False
        return cookie_path.endswith("/") or request_path[len(cookie_path)] == "/"

    def _api_headers(self, cookies: Dict[str, str]) -> Dict[str, str]:
        headers = {
            "User-Agent": self.tab.user_agent,
            "Referer": "https://www.yuque.com/",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        csrf_token = cookies.get("yuque_ctoken")
        return {**headers, **({"X-CSRF-Token": csrf_token} if csrf_token else {})}

    @staticmethod
    def _require_dict_payload(payload: Any, label: str) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise RepositoryResponseError(f"Yuque returned invalid {label} JSON")
        return payload

    @staticmethod
    def _document_from_catalog_item(raw_item: Any, book_id: int) -> Document:
        if not isinstance(raw_item, dict):
            raise RepositoryResponseError("Yuque returned an invalid catalog node")
        title = raw_item.get("title")
        uuid = raw_item.get("uuid")
        raw_parent_uuid = raw_item.get("parent_uuid")
        if raw_parent_uuid is not None and not isinstance(raw_parent_uuid, str):
            raise RepositoryResponseError("Yuque returned an invalid catalog parent")
        parent_uuid = raw_parent_uuid or ""
        node_type = raw_item.get("type", "DOC")
        doc_id = raw_item.get("doc_id") or raw_item.get("id", 0)
        valid_doc_id = (
            node_type == "TITLE"
            or (
                isinstance(doc_id, int)
                and not isinstance(doc_id, bool)
                and doc_id > 0
            )
        )
        if (
            not isinstance(title, str)
            or not title
            or not isinstance(uuid, str)
            or not uuid
            or not isinstance(parent_uuid, str)
            or node_type not in {"DOC", "TITLE"}
            or not valid_doc_id
        ):
            raise RepositoryResponseError("Yuque returned an invalid catalog node")
        return Document.from_api_response(
            {**raw_item, "parent_uuid": parent_uuid, "book_id": book_id}
        )

    @staticmethod
    def _validate_catalog_graph(nodes: List[Document]) -> None:
        node_map = {node.uuid: node for node in nodes}
        if len(node_map) != len(nodes):
            raise RepositoryResponseError("Yuque returned duplicate catalog UUIDs")
        max_depth = 200
        for node in nodes:
            visited = set()
            current = node
            depth = 0
            while current.parent_uuid:
                if current.uuid in visited:
                    raise RepositoryResponseError("Yuque returned a cyclic catalog")
                visited.add(current.uuid)
                parent = node_map.get(current.parent_uuid)
                if parent is None:
                    raise RepositoryResponseError("Yuque returned a dangling catalog parent")
                current = parent
                depth += 1
                if depth > max_depth:
                    raise RepositoryResponseError("Yuque catalog nesting is too deep")

    def _request_api(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        """通用 API 请求封装 (使用 requests + browser cookies)"""
        try:
            cookies = self._yuque_cookies(self.tab.cookies(), url)
            headers = self._api_headers(cookies)
            
            # 合并自定义 headers
            if 'headers' in kwargs:
                headers.update(kwargs.pop('headers'))
            
            response = self.session.request(
                method,
                url,
                cookies=cookies,
                headers=headers,
                timeout=30,
                allow_redirects=False,
                **kwargs,
            )
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 400:
                try:
                    return response.json()
                except (TypeError, ValueError, requests.JSONDecodeError):
                    print("API request failed with status 400")
                    return None
            else:
                print(f"API request failed with status {response.status_code}")
                return None

        except Exception:
            print("API request failed")
            return None
