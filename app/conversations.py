"""用户私有的会话存储；压缩摘要保留原始消息。"""
import json, time
from .db import db


def create(user_id, title):
    with db() as con:
        now = time.time()
        return con.execute('INSERT INTO conversations(user_id,title,created_at,updated_at) VALUES (?,?,?,?)',
                           (user_id, title, now, now)).lastrowid


def list_for(user_id):
    with db() as con:
        return [dict(r) for r in con.execute('SELECT id,title,created_at,updated_at FROM conversations WHERE user_id=? ORDER BY updated_at DESC', (user_id,))]


def get(user_id, conversation_id):
    with db() as con:
        row = con.execute('SELECT id,title,summary,summary_through,created_at,updated_at FROM conversations WHERE id=? AND user_id=?', (conversation_id, user_id)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['messages'] = [dict(r) for r in con.execute('SELECT id,role,content,created_at FROM conversation_messages WHERE conversation_id=? ORDER BY id', (conversation_id,))]
        return result


def add_message(user_id, conversation_id, role, content):
    with db() as con:
        if not con.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?', (conversation_id, user_id)).fetchone():
            return False
        now = time.time()
        con.execute('INSERT INTO conversation_messages(conversation_id,role,content,created_at) VALUES (?,?,?,?)', (conversation_id, role, content, now))
        con.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, conversation_id))
        return True


def add_exchange(user_id, conversation_id, user_content, assistant_content):
    with db() as con:
        if not con.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?', (conversation_id, user_id)).fetchone():
            return False
        now = time.time()
        con.executemany('INSERT INTO conversation_messages(conversation_id,role,content,created_at) VALUES (?,?,?,?)',
                        [(conversation_id, 'user', user_content, now), (conversation_id, 'assistant', assistant_content, now)])
        con.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, conversation_id))
        return True


def set_summary(user_id, conversation_id, summary, through=0):
    with db() as con:
        return bool(con.execute('UPDATE conversations SET summary=?,summary_through=?,updated_at=? WHERE id=? AND user_id=?', (summary, through, time.time(), conversation_id, user_id)).rowcount)


def edit_summary(user_id, conversation_id, summary, expected_summary, expected_through):
    with db() as con:
        return bool(con.execute("UPDATE conversations SET summary=?,updated_at=? WHERE id=? AND user_id=? AND summary_through=? AND summary=? AND summary_through>0", (summary, time.time(), conversation_id, user_id, expected_through, expected_summary)).rowcount)


def compression_batch(summary, messages, limit=30000):
    batch = []
    for message in messages:
        candidate = batch + [message]
        if len(json.dumps({'summary': summary, 'messages': candidate}, ensure_ascii=False)) > limit:
            break
        batch = candidate
    return batch


def delete(user_id, conversation_id):
    with db() as con:
        if not con.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?', (conversation_id, user_id)).fetchone():
            return False
        con.execute("DELETE FROM model_calls WHERE user_id=? AND conversation_id=? AND kind IN ('chat','compress')", (user_id, conversation_id))
        return bool(con.execute('DELETE FROM conversations WHERE id=? AND user_id=?', (conversation_id, user_id)).rowcount)
