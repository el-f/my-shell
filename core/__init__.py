"""my-shell: Cross-platform shell configuration manager."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("my-shell")
except PackageNotFoundError:  # running from a checkout that was never installed
    __version__ = "0+unknown"
