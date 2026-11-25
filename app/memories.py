"""用户手动维护的个人记忆；不自动提升为规则。"""
import time
from .db import db


def create(user_id, content):
    with db() as con:
        return con.execute('INSERT INTO memories(user_id,content,created_at) VALUES (?,?,?)', (user_id, content, time.time())).lastrowid


def list_for(user_id):
    with db() as con:
        return [dict(r) for r in con.execute('SELECT id,content,created_at FROM memories WHERE user_id=? ORDER BY id', (user_id,))]


def selected(user_id, ids):
    allowed = set(ids)
    return [row for row in list_for(user_id) if row['id'] in allowed]


def update(user_id, memory_id, content):
    with db() as con:
        return bool(con.execute('UPDATE memories SET content=? WHERE id=? AND user_id=?', (content, memory_id, user_id)).rowcount)


def delete(user_id, memory_id):
    with db() as con:
        return bool(con.execute('DELETE FROM memories WHERE id=? AND user_id=?', (memory_id, user_id)).rowcount)


def create_project(user_id, content):
    with db() as con:
        now = time.time()
        return con.execute('INSERT INTO project_memories(created_by,content,created_at,updated_at) VALUES (?,?,?,?)', (user_id, content, now, now)).lastrowid


def list_project():
    with db() as con:
        return [dict(row) for row in con.execute('SELECT p.id,p.content,p.created_at,p.updated_at,u.display_name AS author FROM project_memories p JOIN users u ON u.id=p.created_by ORDER BY p.id')]


def selected_project(ids):
    allowed = set(ids)
    return [row for row in list_project() if row['id'] in allowed]


def update_project(memory_id, content):
    with db() as con:
        return bool(con.execute('UPDATE project_memories SET content=?,updated_at=? WHERE id=?', (content, time.time(), memory_id)).rowcount)


def delete_project(memory_id):
    with db() as con:
        return bool(con.execute('DELETE FROM project_memories WHERE id=?', (memory_id,)).rowcount)
