"""Detection and execution wrappers for optional external tools."""

from anydoc.external.libreoffice import convert_with_libreoffice
from anydoc.external.tools import (
    TOOLS,
    ExternalToolError,
    ToolResult,
    ToolSpec,
    available_tools,
    find_tool,
    is_available,
    require_tool,
    run_tool,
    temp_workdir,
    tool_version,
)

__all__ = [
    "TOOLS",
    "ExternalToolError",
    "ToolResult",
    "ToolSpec",
    "convert_with_libreoffice",
    "available_tools",
    "find_tool",
    "is_available",
    "require_tool",
    "run_tool",
    "temp_workdir",
    "tool_version",
]
