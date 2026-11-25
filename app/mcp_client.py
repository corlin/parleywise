"""基于官方 mcp 2.x SDK 的 MCP 客户端（支持 Streamable HTTP 与 stdio）。"""
import asyncio
import json
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

DEFAULT_TIMEOUT = 15
MAX_RESULT_CHARS = 8000


@asynccontextmanager
async def _open_session(server):
    transport = server.get("transport", "http")
    if transport == "stdio":
        command = server.get("command") or ""
        args = server.get("args") or []
        if isinstance(args, str):
            args = json.loads(args)
        params = StdioServerParameters(command=command, args=list(args))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    elif transport == "http":
        url = server.get("url") or ""
        async with streamable_http_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        raise ValueError(f"不支持的 MCP 传输方式：{transport}")


async def _list_tools_async(server, timeout=DEFAULT_TIMEOUT):
    async def _run():
        async with _open_session(server) as session:
            resp = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {"type": "object", "properties": {}},
                }
                for t in resp.tools
            ]

    return await asyncio.wait_for(_run(), timeout=timeout)


async def _call_tool_async(server, tool_name, arguments, timeout=DEFAULT_TIMEOUT, max_chars=MAX_RESULT_CHARS):
    async def _run():
        async with _open_session(server) as session:
            resp = await session.call_tool(tool_name, arguments=arguments or {})
            parts = []
            for item in getattr(resp, "content", []) or []:
                text = getattr(item, "text", None)
                if text is not None:
                    parts.append(text)
                else:
                    parts.append(str(item))
            out = "\n".join(parts).strip()
            if len(out) > max_chars:
                out = out[:max_chars] + "\n…（结果已截断）"
            return {"is_error": bool(getattr(resp, "is_error", getattr(resp, "isError", False))), "content": out}

    return await asyncio.wait_for(_run(), timeout=timeout)


def list_tools(server, timeout=DEFAULT_TIMEOUT):
    """同步列出 MCP Server 工具（供线程池路由调用）。"""
    return asyncio.run(_list_tools_async(server, timeout=timeout))


def call_tool(server, tool_name, arguments=None, timeout=DEFAULT_TIMEOUT, max_chars=MAX_RESULT_CHARS):
    """同步调用 MCP 工具（供线程池路由调用）。"""
    return asyncio.run(_call_tool_async(server, tool_name, arguments, timeout=timeout, max_chars=max_chars))
