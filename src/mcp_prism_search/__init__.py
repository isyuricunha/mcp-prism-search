"""Self-hosted multi-provider search for MCP clients."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("mcp-prism-search")
except PackageNotFoundError:
    # Uninstalled source has no package metadata; never report an invented release.
    __version__ = "0.0.0+unknown"
