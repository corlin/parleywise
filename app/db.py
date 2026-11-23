"""SQLite 存储：一个文件，所有表在这里建。路径可用 HIAGENT_DATA_DIR 改。"""
import json, os, sqlite3, sys, time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from review import load_dotenv  # noqa: E402

load_dotenv()  # 先读 .env，HIAGENT_DATA_DIR 才能生效（命令行管理账号时也一样）
DATA_DIR = Path(os.environ.get("HIAGENT_DATA_DIR", Path(__file__).resolve().parent / "data"))
DB_PATH = DATA_DIR / "hiagent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, pw_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'reviewer',      -- reviewer / admin（admin 可删除材料）
  disabled INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, doc_type TEXT NOT NULL DEFAULT 'public', text TEXT NOT NULL,
  source_filename TEXT, golden_case_id TEXT,
  arm TEXT NOT NULL DEFAULT 'ai',            -- ai：显示 AI 意见；control：对照组，隐藏 AI 意见
  mode TEXT NOT NULL DEFAULT 'full',         -- full：模型流水线；offline：仅关键词
  status TEXT NOT NULL DEFAULT 'queued',     -- queued/running/done/failed
  stage TEXT, error TEXT, pipeline_seconds REAL, usage_json TEXT, model_info TEXT,
  created_at REAL NOT NULL, created_by TEXT, reviewer TEXT,
  review_status TEXT NOT NULL DEFAULT 'pending',  -- pending/in_progress/completed
  review_started_at REAL, review_completed_at REAL, active_ms INTEGER NOT NULL DEFAULT 0,
  conclusion TEXT, summary_note TEXT
);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  source TEXT NOT NULL,                       -- ai / manual
  group_idx INTEGER NOT NULL, rank_in_group INTEGER NOT NULL DEFAULT 0,
  rule_id TEXT NOT NULL, quote TEXT NOT NULL, also_quotes TEXT NOT NULL DEFAULT '[]',
  spans TEXT NOT NULL DEFAULT '[]', reason TEXT, suggestion TEXT, confidence REAL, stage TEXT,
  decision TEXT,                              -- accepted / modified / rejected / NULL
  reject_reason TEXT, note TEXT, edited_suggestion TEXT,
  decided_at REAL, elapsed_ms INTEGER, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dropped (           -- 校验器/复核拦下的意见，留作排查漏检
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (            -- 审计日志：谁在什么时候做了什么
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, finding_id INTEGER,
  type TEXT NOT NULL, payload TEXT, reviewer TEXT, ts REAL NOT NULL
);
"""


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        cols = {r[1] for r in con.execute("PRAGMA table_info(documents)")}
        if "created_by" not in cols:  # 兼容第一版数据库
            con.execute("ALTER TABLE documents ADD COLUMN created_by TEXT")


def log_event(con, doc_id, typ, payload=None, finding_id=None, reviewer=None):
    con.execute("INSERT INTO events(doc_id, finding_id, type, payload, reviewer, ts) VALUES (?,?,?,?,?,?)",
                (doc_id, finding_id, typ, json.dumps(payload, ensure_ascii=False) if payload else None,
                 reviewer, time.time()))
