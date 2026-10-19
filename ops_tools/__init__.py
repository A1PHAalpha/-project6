"""只读运维工具入口，供本地 API、LLM 工具调用或 MCP 适配层复用。"""
from .readonly_tools import invoke_tool, list_tool_definitions

__all__ = ["invoke_tool", "list_tool_definitions"]
