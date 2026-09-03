__all__ = ["__version__"]

try:
    from core.version import __version__
except ImportError:  # pragma: no cover - harness installed without src on path
    __version__ = "0.1.0"
