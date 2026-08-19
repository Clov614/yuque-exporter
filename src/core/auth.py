"""
语雀凭证持久化管理
===================
负责保存和恢复浏览器 cookies，实现免重复登录
"""

import contextlib
import getpass
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urlparse


_ACL_THREAD_LOCK = threading.RLock()
_ACL_LOCAL = threading.local()
_VERIFIED_CREDENTIAL_PATHS: set[tuple[str, bool]] = set()


@contextlib.contextmanager
def _acl_lock(path: Path):
    canonical = str(path.resolve())
    held_paths = getattr(_ACL_LOCAL, "held_paths", None)
    if held_paths is None:
        held_paths = set()
        _ACL_LOCAL.held_paths = held_paths
    if canonical in held_paths:
        yield
        return

    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    lock_dir = Path(tempfile.gettempdir()) / "yuque-exporter-acl-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        lock_dir.chmod(0o700)
    lock_path = lock_dir / f"{digest}.lock"
    with _ACL_THREAD_LOCK:
        held_paths.add(canonical)
        try:
            if os.name == "nt":
                import ctypes

                kernel32 = ctypes.windll.kernel32
                mutex = kernel32.CreateMutexW(None, False, f"Local\\YuqueExporter-{digest}")
                if not mutex:
                    raise OSError("failed to create credentials mutex")
                wait_result = kernel32.WaitForSingleObject(mutex, 30000)
                if wait_result not in {0, 0x80}:
                    kernel32.CloseHandle(mutex)
                    raise TimeoutError("timed out waiting for credentials lock")
                try:
                    yield
                finally:
                    kernel32.ReleaseMutex(mutex)
                    kernel32.CloseHandle(mutex)
                return

            import fcntl

            file_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(file_descriptor, "a+b") as lock_file:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            held_paths.remove(canonical)


def credentials_lock(path: Path):
    """Serialize credential mutations for one profile path."""
    return _acl_lock(path)


class LoginStatus(Enum):
    """登录状态枚举"""
    LOGGED_IN = auto()  # 已登录且会话有效
    EXPIRED = auto()    # 曾登录但会话失效
    NONE = auto()       # 未登录/无凭证

def secure_credentials_path(path: Path, *, directory: bool) -> None:
    """Restrict a credentials path while serializing ACL changes."""
    with _acl_lock(path):
        _secure_credentials_path_unlocked(path, directory=directory)
        _VERIFIED_CREDENTIAL_PATHS.add((str(path.resolve()), directory))


def ensure_secure_credentials_path(path: Path, *, directory: bool) -> None:
    """Harden an existing path once per process, failing closed."""
    key = (str(path.resolve()), directory)
    with _ACL_THREAD_LOCK:
        if key in _VERIFIED_CREDENTIAL_PATHS:
            return
    secure_credentials_path(path, directory=directory)


def _secure_credentials_path_unlocked(path: Path, *, directory: bool) -> None:
    """Apply owner-only permissions to one credentials path."""
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return

    username = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    identity = f"{domain}\\{username}" if domain else username
    permission = "(OI)(CI)F" if directory else "F"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    commands = (
        ["icacls", str(path), "/reset"],
        ["icacls", str(path), "/inheritance:r"],
        ["icacls", str(path), "/grant:r", f"{identity}:{permission}"],
    )
    for command in commands:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            creationflags=creation_flags,
        )
    _verify_windows_acl(path, creation_flags)


def _verify_windows_acl(path: Path, creation_flags: int) -> None:
    escaped_path = str(path).replace("'", "''")
    script = """
$target = '__TARGET__'
$acl = Get-Acl -LiteralPath $target
$current = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$rules = @($acl.Access)
$foreign = @($rules | Where-Object {
  $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -ne $current
})
$ownerRules = @($rules | Where-Object {
  $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $current -and
  $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
  ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl)
})
if ($foreign.Count -gt 0 -or $ownerRules.Count -eq 0) { exit 1 }
""".replace("__TARGET__", escaped_path)
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        creationflags=creation_flags,
    )


def is_authenticated_yuque_url(value: str) -> bool:
    """Return true only for authenticated Yuque dashboard/user paths."""
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        return (
            parsed.scheme.lower() == "https"
            and host in {"yuque.com", "www.yuque.com"}
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
            and (path == "/dashboard" or path.startswith("/dashboard/"))
        )
    except (ValueError, UnicodeError):
        return False


class YuqueAuth:
    """
    语雀凭证管理器
    """
    
    # 凭证存储路径
    CREDENTIALS_DIR = Path.home() / ".yuque"
    COOKIES_FILE = CREDENTIALS_DIR / "cookies.json"
    
    # 语雀关键 URL
    DASHBOARD_URL = "https://www.yuque.com/dashboard"
    
    def __init__(self, cookies_file: Optional[Path] = None):
        """初始化凭证路径；权限在创建或写入时加固。"""
        if cookies_file is not None:
            self.COOKIES_FILE = Path(cookies_file).expanduser()
            self.CREDENTIALS_DIR = self.COOKIES_FILE.parent
        directory_created = not self.CREDENTIALS_DIR.exists()
        self.CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        if directory_created:
            secure_credentials_path(self.CREDENTIALS_DIR, directory=True)
        else:
            ensure_secure_credentials_path(self.CREDENTIALS_DIR, directory=True)
        if self.COOKIES_FILE.exists():
            ensure_secure_credentials_path(self.COOKIES_FILE, directory=False)
    
    def save_cookies(self, tab) -> bool:
        """原子保存 cookies，并序列化同 profile 的凭据变更。"""
        with _acl_lock(self.COOKIES_FILE):
            return self._save_cookies_unlocked(tab)

    def _save_cookies_unlocked(self, tab) -> bool:
        temporary_path: Optional[Path] = None
        replaced = False
        try:
            secure_credentials_path(self.CREDENTIALS_DIR, directory=True)
            data = {
                "saved_at": datetime.now().isoformat(),
                "cookies": tab.cookies(),
            }
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.CREDENTIALS_DIR,
                prefix=".cookies.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temporary_path = Path(temp_file.name)
                secure_credentials_path(temporary_path, directory=False)
                json.dump(data, temp_file, ensure_ascii=False, indent=2)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temporary_path, self.COOKIES_FILE)
            temporary_path = None
            replaced = True
            secure_credentials_path(self.COOKIES_FILE, directory=False)
            return True
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            if replaced and self.COOKIES_FILE.exists():
                try:
                    self.COOKIES_FILE.unlink()
                except OSError:
                    pass
            print("❌ 保存 Cookies 失败")
            return False
        finally:
            if temporary_path and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
    
    def load_cookies(self, tab) -> bool:
        """清空现有浏览器会话后，仅加载当前凭据文件。"""
        try:
            tab.run_cdp("Network.clearBrowserCookies")
        except Exception:
            return False
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
            
            if is_authenticated_yuque_url(current_url):
                return LoginStatus.LOGGED_IN
            return LoginStatus.EXPIRED
            
        except Exception as e:
            print(f"❌ 检查登录状态出错: {e}")
            return LoginStatus.NONE
    
    def clear_credentials(self, *, _locked: bool = False) -> bool:
        """清除已保存的凭证。"""
        lock = contextlib.nullcontext() if _locked else _acl_lock(self.COOKIES_FILE)
        try:
            with lock:
                if self.COOKIES_FILE.exists():
                    self.COOKIES_FILE.unlink()
            return True
        except Exception:
            print("❌ 清除凭证失败")
            return False
