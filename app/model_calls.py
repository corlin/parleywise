"""模型网关调用元数据；不保存提示词、响应、密钥或网关错误正文。"""
import time
from .db import db


def record(event, *, user_id=None, kind, conversation_id=None, document_id=None, stage=None, run_id=None):
    with db() as con:
        con.execute('''INSERT INTO model_calls(user_id,kind,conversation_id,document_id,run_id,stage,requested_model,
                    served_model,status,attempt,started_at,duration_ms,prompt_tokens,completion_tokens,cost_usd)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (user_id, kind, conversation_id, document_id, run_id, stage, event.get('requested_model', ''),
                     event.get('served_model'), event['status'], event.get('attempt', 1), event.get('started_at', time.time()),
                     event.get('duration_ms', 0), event.get('prompt_tokens'), event.get('completion_tokens'), event.get('cost_usd')))


def visible_to(user_id, limit=200, conversation_id=None, document_id=None):
    where = "(kind='review' OR (user_id=? AND kind IN ('chat','compress')))"
    params = [user_id]
    if conversation_id is not None:
        where += " AND user_id=? AND kind IN ('chat','compress') AND conversation_id=?"
        params.extend([user_id, conversation_id])
    if document_id is not None:
        where += " AND kind='review' AND document_id=?"
        params.append(document_id)
    params.append(limit)
    with db() as con:
        return [dict(r) for r in con.execute('''SELECT id,kind,conversation_id,document_id,run_id,stage,requested_model,
                served_model,status,attempt,started_at,duration_ms,prompt_tokens,completion_tokens,cost_usd
                FROM model_calls WHERE ''' + where + ' ORDER BY id DESC LIMIT ?', params)]
