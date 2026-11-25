#!/usr/bin/env python3
"""Parleywise 只读合规规则检索 MCP Server（基于官方 mcp 2.x SDK）。

用法：
  uv run python tools/compliance_mcp.py                  # stdio 传输（默认）
  uv run python tools/compliance_mcp.py --http           # Streamable HTTP 传输
"""
import argparse
import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "rules.json"

mcp = MCPServer("parleywise-compliance")


def _load():
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


@mcp.tool()
def list_rule_sources() -> str:
    """列出当前合规规则库覆盖的法规来源与版本说明。"""
    data = _load()
    return json.dumps(
        {
            "version": data.get("version"),
            "as_of": data.get("as_of"),
            "note": data.get("note"),
            "sources": data.get("sources", {}),
        },
        ensure_ascii=False,
    )


@mcp.tool()
def search_rules(query: str, doc_type: str = "") -> str:
    """按编号、标题、描述或关键词检索基金营销合规规则。doc_type 可选 public 或 private。"""
    data = _load()
    kw = (query or "").strip().lower()
    matches = []
    for r in data.get("rules", []):
        if doc_type and r.get("applies_to", doc_type) != doc_type:
            continue
        hay = " ".join([
            r.get("id", ""),
            r.get("title", ""),
            r.get("article", ""),
            r.get("description", ""),
            " ".join(r.get("keywords", [])),
        ]).lower()
        if not kw or kw in hay:
            matches.append({
                "id": r["id"],
                "title": r["title"],
                "article": r.get("article"),
                "severity": r.get("severity"),
                "applies_to": r.get("applies_to", "all"),
            })
    return json.dumps({"count": len(matches), "rules": matches[:20]}, ensure_ascii=False)


@mcp.tool()
def get_rule(rule_id: str) -> str:
    """根据规则编号（如 R01、R18）获取单条规则的完整条款依据与说明。"""
    data = _load()
    rid = (rule_id or "").strip().upper()
    for r in data.get("rules", []):
        if r.get("id") == rid:
            return json.dumps(r, ensure_ascii=False)
    return json.dumps({"error": f"未找到规则 {rid}"}, ensure_ascii=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    args = ap.parse_args()
    mcp.run(transport="streamable-http" if args.http else "stdio")
