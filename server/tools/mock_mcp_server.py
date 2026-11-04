"""Mock MCP 服务器（stdio 传输）——端到端验证用。

提供两个工具：
- echo(text): 原样返回文本
- word_count(text): 统计字符数与中文数

启动方式：python tools/mock_mcp_server.py（由 MCP 客户端以子进程拉起）
"""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("mock-tools")


@mcp.tool()
def echo(text: str) -> str:
    """原样返回输入文本，用于验证 MCP 链路。"""
    return f"echo: {text}"


@mcp.tool()
def word_count(text: str) -> str:
    """统计文本的字符数与中文字符数。"""
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return f"总字符 {len(text)}，中文字符 {cn}"


if __name__ == "__main__":
    mcp.run()
