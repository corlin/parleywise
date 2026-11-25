"""MCP 服务配置存储：支持个人 HTTP(S) 服务与管理员配置的工作区 stdio 服务。"""
import json
import time

from .db import db


def _row_to_dict(r):
    d = dict(r)
    d["args"] = json.loads(d.pop("args_json", None) or "[]")
    d["tools"] = json.loads(d.pop("tools_json", None) or "[]")
    return d


def create(user_id, name, url="", *, transport="http", command="", args=None, scope="personal"):
    with db() as con:
        return con.execute(
            "INSERT INTO mcp_servers(user_id,name,url,transport,command,args_json,scope,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (user_id, name, url, transport, command, json.dumps(args or [], ensure_ascii=False), scope, time.time()),
        ).lastrowid


def list_for(user_id):
    with db() as con:
        rows = con.execute(
            "SELECT id,user_id,name,url,transport,command,args_json,scope,tools_json,last_checked_at,created_at"
            " FROM mcp_servers WHERE user_id=? OR scope='workspace' ORDER BY id",
            (user_id,),
        )
        return [_row_to_dict(r) for r in rows]


def get_visible(user_id, server_id):
    with db() as con:
        row = con.execute(
            "SELECT id,user_id,name,url,transport,command,args_json,scope,tools_json,last_checked_at,created_at"
            " FROM mcp_servers WHERE id=? AND (user_id=? OR scope='workspace')",
            (server_id, user_id),
        ).fetchone()
        return _row_to_dict(row) if row else None


def selected(user_id, server_ids):
    if not server_ids:
        return []
    marks = ",".join("?" * len(server_ids))
    with db() as con:
        rows = con.execute(
            f"SELECT id,user_id,name,url,transport,command,args_json,scope,tools_json,last_checked_at,created_at"
            f" FROM mcp_servers WHERE id IN ({marks}) AND (user_id=? OR scope='workspace') ORDER BY id",
            (*server_ids, user_id),
        )
        return [_row_to_dict(r) for r in rows]


def save_tools(server_id, tools):
    with db() as con:
        con.execute(
            "UPDATE mcp_servers SET tools_json=?, last_checked_at=? WHERE id=?",
            (json.dumps(tools, ensure_ascii=False), time.time(), server_id),
        )


def delete(user_id, server_id, *, is_admin=False):
    with db() as con:
        if is_admin:
            return bool(con.execute(
                "DELETE FROM mcp_servers WHERE id=? AND (user_id=? OR scope='workspace')",
                (server_id, user_id),
            ).rowcount)
        return bool(con.execute(
            "DELETE FROM mcp_servers WHERE id=? AND user_id=? AND scope='personal'",
            (server_id, user_id),
        ).rowcount)
