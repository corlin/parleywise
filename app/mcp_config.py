"""仅保存 MCP 地址；不建立连接，也不执行工具。"""
import time
from .db import db


def create(user_id, name, url):
    with db() as con:
        return con.execute('INSERT INTO mcp_servers(user_id,name,url,created_at) VALUES (?,?,?,?)', (user_id, name, url, time.time())).lastrowid


def list_for(user_id):
    with db() as con:
        return [dict(r) for r in con.execute('SELECT id,name,url,created_at FROM mcp_servers WHERE user_id=? ORDER BY id', (user_id,))]


def delete(user_id, server_id):
    with db() as con:
        return bool(con.execute('DELETE FROM mcp_servers WHERE id=? AND user_id=?', (server_id, user_id)).rowcount)
