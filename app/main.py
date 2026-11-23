"""hiagent MVP1 复核工作台后端：FastAPI + SQLite，审查流水线直接复用 review.py。

启动：uv run uvicorn app.main:app --port 8000
"""
import io, json, os, sqlite3, sys, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402

from . import report  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("HIAGENT_DATA_DIR", APP_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "hiagent.db"
MAX_CHARS = 30000
REJECT_REASONS = {"not_violation": "不构成违规", "not_applicable": "规则不适用",
                  "bad_quote": "片段定位不准", "duplicate": "与其他意见重复", "other": "其他"}
CONCLUSIONS = {"pass": "通过", "revise": "修改后通过", "reject": "不通过"}

review.load_dotenv()
RULES = review.load_rules()
CHEAP = os.environ.get("HIAGENT_MODEL_CHEAP", "cheap")
STRONG = os.environ.get("HIAGENT_MODEL_STRONG", "strong")

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, doc_type TEXT NOT NULL DEFAULT 'public', text TEXT NOT NULL,
  source_filename TEXT, golden_case_id TEXT,
  arm TEXT NOT NULL DEFAULT 'ai',            -- ai：显示 AI 意见；control：对照组，隐藏 AI 意见
  mode TEXT NOT NULL DEFAULT 'full',         -- full：模型流水线；offline：仅关键词
  status TEXT NOT NULL DEFAULT 'queued',     -- queued/running/done/failed
  stage TEXT, error TEXT, pipeline_seconds REAL, usage_json TEXT, model_info TEXT,
  created_at REAL NOT NULL, reviewer TEXT,
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
CREATE TABLE IF NOT EXISTS dropped (
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
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


with db() as _c:
    _c.executescript(SCHEMA)
    _c.execute("PRAGMA journal_mode=WAL")


def log_event(con, doc_id, typ, payload=None, finding_id=None, reviewer=None):
    con.execute("INSERT INTO events(doc_id, finding_id, type, payload, reviewer, ts) VALUES (?,?,?,?,?,?)",
                (doc_id, finding_id, typ, json.dumps(payload, ensure_ascii=False) if payload else None,
                 reviewer, time.time()))


# ---------- 审查任务 ----------
EXECUTOR = ThreadPoolExecutor(max_workers=2)


class StageLLM(review.LLM):
    """第 1 次调用是初筛，第 2 次是复核；调用前把阶段写库，供前端显示。"""
    def __init__(self, doc_id):
        super().__init__()
        self.doc_id, self.n = doc_id, 0

    def chat(self, model, system, user):
        self.n += 1
        set_doc(self.doc_id, stage="triage" if self.n == 1 else "review")
        return super().chat(model, system, user)


def set_doc(doc_id, **kw):
    with db() as con:
        con.execute(f"UPDATE documents SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), doc_id))


def run_pipeline(doc_id):
    with db() as con:
        d = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    t0 = time.time()
    set_doc(doc_id, status="running", stage="keyword", error=None)
    try:
        text = d["text"]
        rules = review.applicable(RULES, d["doc_type"])
        hints = review.keyword_scan(text, rules)
        found = [h for h in hints if rules[h["rule_id"]]["check"] == "keyword"]
        rejected, llm = [], None
        if d["mode"] == "full":
            llm = StageLLM(doc_id)
            if not llm.key:
                raise RuntimeError("未设置 LITELLM_API_KEY（mvp0/.env）")
            reviewed, rejected = review.llm_review(text, rules, hints, llm, CHEAP, STRONG)
            found += reviewed
        set_doc(doc_id, stage="verify")
        kept, dropped = review.verify(found, text, rules)
        groups = review.group_snippets(kept, text, rules)
        now = time.time()
        with db() as con:
            con.execute("DELETE FROM findings WHERE doc_id=? AND source='ai'", (doc_id,))
            con.execute("DELETE FROM dropped WHERE doc_id=?", (doc_id,))
            for gi, g in enumerate(groups):
                for ri, f in enumerate(g):
                    qs = [f["quote"]] + list(f.get("also_quotes", []))
                    spans = [s for s in (review._span(q, text) for q in qs) if s]
                    con.execute(
                        "INSERT INTO findings(doc_id, source, group_idx, rank_in_group, rule_id, quote, also_quotes,"
                        " spans, reason, suggestion, confidence, stage, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (doc_id, "ai", gi, ri, f["rule_id"], f["quote"],
                         json.dumps(f.get("also_quotes", []), ensure_ascii=False), json.dumps(spans),
                         f.get("reason", ""), f.get("suggestion", ""), _num(f.get("confidence")),
                         f.get("stage"), now))
            for x in dropped + rejected:
                con.execute("INSERT INTO dropped(doc_id, payload) VALUES (?,?)",
                            (doc_id, json.dumps(x, ensure_ascii=False)))
            con.execute("UPDATE documents SET status='done', stage=NULL, pipeline_seconds=?, usage_json=?,"
                        " model_info=? WHERE id=?",
                        (round(time.time() - t0, 1), json.dumps(llm.usage) if llm else None,
                         f"{CHEAP} → {STRONG}" if llm else "仅关键词", doc_id))
            log_event(con, doc_id, "pipeline_done", {"findings": len(kept), "seconds": round(time.time() - t0, 1)})
    except Exception as e:  # 失败写库，前端可重试
        traceback.print_exc()
        set_doc(doc_id, status="failed", stage=None, error=str(e)[:500], pipeline_seconds=round(time.time() - t0, 1))


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def enqueue(doc_id):
    set_doc(doc_id, status="queued", stage=None, error=None)
    EXECUTOR.submit(run_pipeline, doc_id)


# 服务重启时，把中断的任务重新排队
with db() as _c:
    _stuck = [r[0] for r in _c.execute("SELECT id FROM documents WHERE status IN ('queued','running')")]
for _id in _stuck:
    enqueue(_id)


# ---------- 文件解析 ----------
# PDF 字体常把汉字映射成部首字符（如 ⻛ ⾦），会让关键词和片段匹配失败
_RADICALS = dict(zip("⻛⻔⻢⻓⻋⻅⻆⻝⻘⻜⻄⻩⻰⻮⻉⻈⻨⻥⻚⻧⻦⻤⻖⻗⻙", "风门马长车见角食青飞西黄龙齿贝讠麦鱼页卤镸鬼阝雨纟"))


def fix_pdf_radicals(t):
    import unicodedata
    return "".join(unicodedata.normalize("NFKC", c) if 0x2F00 <= ord(c) <= 0x2FDF else _RADICALS.get(c, c) for c in t)


def extract_text(filename, data):
    name = (filename or "").lower()
    if name.endswith((".txt", ".md")):
        for enc in ("utf-8-sig", "gb18030"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        raise HTTPException(400, "无法识别文本编码，请另存为 UTF-8")
    if name.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        txt = fix_pdf_radicals("\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages))
        if not txt.strip():
            raise HTTPException(400, "PDF 里没有可提取的文字，可能是扫描件；暂不支持 OCR")
        return txt
    raise HTTPException(400, "仅支持 .txt / .md / .docx / .pdf")


def clean_text(t):
    lines = [l.rstrip() for l in t.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out = "\n".join(lines).strip()
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out


# ---------- 序列化 ----------
def finding_dict(r, rules=RULES):
    f = dict(r)
    f["also_quotes"] = json.loads(f["also_quotes"] or "[]")
    f["spans"] = json.loads(f["spans"] or "[]")
    rule = rules.get(f["rule_id"], {})
    f["rule"] = {k: rule.get(k) for k in ("id", "title", "article", "source", "description", "severity", "check")}
    return f


def doc_summary(con, d):
    c = con.execute("""SELECT
        SUM(source='ai') ai, SUM(source='ai' AND decision IS NOT NULL) ai_decided,
        SUM(source='manual') manual, COUNT(DISTINCT CASE WHEN source='ai' THEN group_idx END) groups
        FROM findings WHERE doc_id=?""", (d["id"],)).fetchone()
    s = {k: d[k] for k in ("id", "title", "doc_type", "arm", "mode", "status", "stage", "error", "created_at",
                           "review_status", "active_ms", "conclusion", "reviewer", "golden_case_id",
                           "pipeline_seconds", "source_filename")}
    s.update(ai_findings=c["ai"] or 0, ai_decided=c["ai_decided"] or 0, manual_findings=c["manual"] or 0,
             snippets=c["groups"] or 0)
    return s


def load_doc(con, doc_id):
    d = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "材料不存在")
    return d


# ---------- API ----------
app = FastAPI(title="hiagent 复核工作台")


@app.middleware("http")
async def no_cache_static(request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"  # 单机工具，改了前端刷新即生效
    return resp


@app.get("/api/meta")
def meta():
    return {"reject_reasons": REJECT_REASONS, "conclusions": CONCLUSIONS,
            "models": f"{CHEAP} → {STRONG}", "key_configured": bool(os.environ.get("LITELLM_API_KEY")),
            "sources": json.loads((ROOT / "rules.json").read_text(encoding="utf-8")).get("sources", {})}


@app.get("/api/rules")
def rules():
    return list(RULES.values())


@app.get("/api/documents")
def list_documents():
    with db() as con:
        return [doc_summary(con, d) for d in con.execute("SELECT * FROM documents ORDER BY id DESC")]


@app.post("/api/documents")
async def create_document(title: str = Form(""), doc_type: str = Form("public"), text: str = Form(""),
                          arm: str = Form("ai"), mode: str = Form("full"), reviewer: str = Form(""),
                          golden_case_id: str = Form(""), file: UploadFile | None = File(None)):
    fname = None
    if file is not None and file.filename:
        fname = file.filename
        text = extract_text(fname, await file.read())
    text = clean_text(text or "")
    if not text:
        raise HTTPException(400, "材料内容为空")
    if len(text) > MAX_CHARS:
        raise HTTPException(400, f"材料超过 {MAX_CHARS} 字，请拆分后提交")
    if doc_type not in ("public", "private") or arm not in ("ai", "control") or mode not in ("full", "offline"):
        raise HTTPException(400, "参数不合法")
    title = title.strip() or (Path(fname).stem if fname else text[:24].replace("\n", " "))
    with db() as con:
        cur = con.execute("INSERT INTO documents(title, doc_type, text, source_filename, golden_case_id, arm, mode,"
                          " created_at, reviewer) VALUES (?,?,?,?,?,?,?,?,?)",
                          (title, doc_type, text, fname, golden_case_id or None, arm, mode, time.time(),
                           reviewer or None))
        doc_id = cur.lastrowid
        log_event(con, doc_id, "created", {"arm": arm, "mode": mode, "file": fname}, reviewer=reviewer or None)
    enqueue(doc_id)
    return {"id": doc_id}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int):
    with db() as con:
        d = load_doc(con, doc_id)
        out = doc_summary(con, d)
        out.update(text=d["text"], summary_note=d["summary_note"], usage=json.loads(d["usage_json"] or "null"),
                   model_info=d["model_info"], review_started_at=d["review_started_at"],
                   review_completed_at=d["review_completed_at"])
        rows = con.execute("SELECT * FROM findings WHERE doc_id=? ORDER BY group_idx, rank_in_group, id", (doc_id,))
        fs = [finding_dict(r) for r in rows]
        # 对照组审查完成前，不把 AI 意见发给前端
        hide_ai = d["arm"] == "control" and d["review_status"] != "completed"
        out["ai_hidden"] = hide_ai
        out["findings"] = [f for f in fs if not (hide_ai and f["source"] == "ai")]
        out["dropped_count"] = con.execute("SELECT COUNT(*) FROM dropped WHERE doc_id=?", (doc_id,)).fetchone()[0]
        if d["golden_case_id"] and d["review_status"] == "completed":  # 完成前不给前端，避免影响审核人
            out["golden"] = golden_cases().get(d["golden_case_id"])
    return out


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int):
    with db() as con:
        load_doc(con, doc_id)
        con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        con.execute("DELETE FROM dropped WHERE doc_id=?", (doc_id,))
        log_event(con, doc_id, "deleted")
    return {"ok": True}


@app.post("/api/documents/{doc_id}/retry")
def retry(doc_id: int):
    with db() as con:
        d = load_doc(con, doc_id)
        if d["status"] in ("queued", "running"):
            raise HTTPException(409, "任务正在进行")
    enqueue(doc_id)
    return {"ok": True}


def _editable(d):
    if d["review_status"] == "completed":
        raise HTTPException(409, "审查已完成，如需修改请先重新打开")
    if d["status"] != "done":
        raise HTTPException(409, "AI 初审尚未完成")


@app.post("/api/documents/{doc_id}/heartbeat")
async def heartbeat(doc_id: int, request: Request):
    body = await request.json()
    delta = max(0, min(int(body.get("active_ms", 0)), 30000))  # 单次最多记 30 秒，防止异常值
    with db() as con:
        d = load_doc(con, doc_id)
        if d["review_status"] == "completed" or d["status"] != "done":
            return {"active_ms": d["active_ms"]}
        con.execute("UPDATE documents SET active_ms=active_ms+?, review_status='in_progress',"
                    " review_started_at=COALESCE(review_started_at, ?), reviewer=COALESCE(?, reviewer) WHERE id=?",
                    (delta, time.time(), body.get("reviewer") or None, doc_id))
        return {"active_ms": d["active_ms"] + delta}


@app.patch("/api/findings/{fid}")
async def decide(fid: int, request: Request):
    body = await request.json()
    with db() as con:
        f = con.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone()
        if not f:
            raise HTTPException(404, "意见不存在")
        d = load_doc(con, f["doc_id"])
        _editable(d)
        decision = body.get("decision")
        if decision not in ("accepted", "modified", "rejected", None):
            raise HTTPException(400, "decision 不合法")
        rr = body.get("reject_reason")
        if decision == "rejected" and rr not in REJECT_REASONS:
            raise HTTPException(400, "驳回须选择原因")
        if decision == "rejected" and rr == "other" and not (body.get("note") or "").strip():
            raise HTTPException(400, "选择“其他”时请填写说明")
        if decision == "modified" and not (body.get("edited_suggestion") or "").strip():
            raise HTTPException(400, "修改时请填写修改后的意见")
        con.execute("UPDATE findings SET decision=?, reject_reason=?, note=?, edited_suggestion=?, decided_at=?,"
                    " elapsed_ms=COALESCE(elapsed_ms,0)+? WHERE id=?",
                    (decision, rr if decision == "rejected" else None, (body.get("note") or "").strip() or None,
                     (body.get("edited_suggestion") or "").strip() if decision == "modified" else None,
                     time.time() if decision else None, max(0, int(body.get("elapsed_ms") or 0)), fid))
        log_event(con, f["doc_id"], "decision", {k: body.get(k) for k in
                  ("decision", "reject_reason", "note", "edited_suggestion", "elapsed_ms")},
                  finding_id=fid, reviewer=body.get("reviewer"))
        return finding_dict(con.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone())


@app.post("/api/documents/{doc_id}/findings")
async def add_manual(doc_id: int, request: Request):
    body = await request.json()
    with db() as con:
        d = load_doc(con, doc_id)
        _editable(d)
        rid = body.get("rule_id")
        if rid not in RULES:
            raise HTTPException(400, "请选择规则")
        s, e = int(body.get("start", -1)), int(body.get("end", -1))
        if not (0 <= s < e <= len(d["text"])):
            raise HTTPException(400, "请先在原文中划选片段")
        quote = d["text"][s:e]
        gi = (con.execute("SELECT MAX(group_idx) FROM findings WHERE doc_id=?", (doc_id,)).fetchone()[0] or 0) + 1000
        cur = con.execute(
            "INSERT INTO findings(doc_id, source, group_idx, rule_id, quote, spans, reason, suggestion, decision,"
            " decided_at, elapsed_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, "manual", gi, rid, quote, json.dumps([[s, e]]), (body.get("reason") or "").strip(),
             (body.get("suggestion") or "").strip(), "accepted", time.time(),
             max(0, int(body.get("elapsed_ms") or 0)), time.time()))
        log_event(con, doc_id, "manual_add", {"rule_id": rid, "quote": quote}, finding_id=cur.lastrowid,
                  reviewer=body.get("reviewer"))
        return finding_dict(con.execute("SELECT * FROM findings WHERE id=?", (cur.lastrowid,)).fetchone())


@app.delete("/api/findings/{fid}")
def delete_manual(fid: int):
    with db() as con:
        f = con.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone()
        if not f or f["source"] != "manual":
            raise HTTPException(400, "只能删除人工补充的问题")
        _editable(load_doc(con, f["doc_id"]))
        con.execute("DELETE FROM findings WHERE id=?", (fid,))
        log_event(con, f["doc_id"], "manual_delete", {"rule_id": f["rule_id"]}, finding_id=fid)
    return {"ok": True}


@app.post("/api/documents/{doc_id}/complete")
async def complete(doc_id: int, request: Request):
    body = await request.json()
    with db() as con:
        d = load_doc(con, doc_id)
        _editable(d)
        if body.get("conclusion") not in CONCLUSIONS:
            raise HTTPException(400, "请选择审查结论")
        if d["arm"] == "ai":
            left = con.execute("SELECT COUNT(*) FROM findings WHERE doc_id=? AND source='ai' AND decision IS NULL",
                               (doc_id,)).fetchone()[0]
            if left:
                raise HTTPException(400, f"还有 {left} 条 AI 意见未处理")
        con.execute("UPDATE documents SET review_status='completed', review_completed_at=?, conclusion=?,"
                    " summary_note=?, reviewer=COALESCE(?, reviewer),"
                    " review_started_at=COALESCE(review_started_at, ?) WHERE id=?",
                    (time.time(), body["conclusion"], (body.get("summary_note") or "").strip() or None,
                     body.get("reviewer") or None, time.time(), doc_id))
        log_event(con, doc_id, "completed", {"conclusion": body["conclusion"], "active_ms": d["active_ms"]},
                  reviewer=body.get("reviewer"))
    return {"ok": True}


@app.post("/api/documents/{doc_id}/reopen")
def reopen(doc_id: int):
    with db() as con:
        d = load_doc(con, doc_id)
        if d["arm"] == "control":
            raise HTTPException(409, "对照组完成后已看过 AI 意见，不能重新打开，否则计时和结果会被污染")
        con.execute("UPDATE documents SET review_status='in_progress', review_completed_at=NULL WHERE id=?",
                    (doc_id,))
        log_event(con, doc_id, "reopened")
    return {"ok": True}


@app.get("/api/documents/{doc_id}/report.{fmt}")
def export_report(doc_id: int, fmt: str):
    data = get_document(doc_id)
    with db() as con:
        data["events"] = [dict(r) for r in con.execute(
            "SELECT type, payload, reviewer, ts FROM events WHERE doc_id=? ORDER BY id", (doc_id,))]
    fname = f"审查报告_{doc_id}"
    if fmt == "html":
        return HTMLResponse(report.to_html(data, REJECT_REASONS, CONCLUSIONS))
    if fmt == "md":
        return PlainTextResponse(report.to_md(data, REJECT_REASONS, CONCLUSIONS), media_type="text/markdown",
                                 headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_q(fname)}.md"})
    if fmt == "json":
        return JSONResponse(data, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_q(fname)}.json"})
    raise HTTPException(404)


def _q(s):
    from urllib.parse import quote
    return quote(s)


# ---------- 度量 ----------
@app.get("/api/metrics")
def metrics():
    with db() as con:
        docs = [dict(d) for d in con.execute("SELECT * FROM documents WHERE review_status='completed'")]
        fs = [dict(f) for f in con.execute(
            "SELECT f.* FROM findings f JOIN documents d ON d.id=f.doc_id WHERE d.review_status='completed'")]
    by_doc = {}
    for f in fs:
        by_doc.setdefault(f["doc_id"], []).append(f)
    arms = {}
    for arm in ("ai", "control"):
        ds = [d for d in docs if d["arm"] == arm]
        mins = [d["active_ms"] / 60000 for d in ds]
        ai = [f for d in ds for f in by_doc.get(d["id"], []) if f["source"] == "ai"]
        manual = [f for d in ds for f in by_doc.get(d["id"], []) if f["source"] == "manual"]
        a = {"docs": len(ds), "avg_minutes": round(sum(mins) / len(mins), 2) if mins else None,
             "median_minutes": round(sorted(mins)[len(mins) // 2], 2) if mins else None,
             "manual_findings": len(manual),
             "manual_per_doc": round(len(manual) / len(ds), 2) if ds else None}
        if arm == "ai":
            dec = {k: sum(1 for f in ai if f["decision"] == k) for k in ("accepted", "modified", "rejected")}
            reasons = {}
            for f in ai:
                if f["decision"] == "rejected":
                    reasons[f["reject_reason"]] = reasons.get(f["reject_reason"], 0) + 1
            total = sum(dec.values())
            a.update(ai_findings=len(ai), decisions=dec, reject_reasons=reasons,
                     adoption_rate=round((dec["accepted"] + dec["modified"]) / total, 3) if total else None)
        else:
            # 对照组：AI 也跑了但对审核人隐藏，可比较两边各自发现了什么（按 文档+规则 对齐）
            human = {(f["doc_id"], f["rule_id"]) for f in manual}
            aiset = {(f["doc_id"], f["rule_id"]) for f in ai}
            a.update(ai_findings_hidden=len(ai), human_issue_rules=len(human),
                     ai_also_found=len(human & aiset), ai_only=len(aiset - human))
        arms[arm] = a
    per_rule = {}
    for d in docs:
        if d["arm"] != "ai":
            continue
        for f in by_doc.get(d["id"], []):
            r = per_rule.setdefault(f["rule_id"], {"rule_id": f["rule_id"], "title": RULES.get(f["rule_id"], {}).get("title"),
                                                   "ai": 0, "adopted": 0, "rejected": 0, "manual": 0})
            if f["source"] == "manual":
                r["manual"] += 1
            else:
                r["ai"] += 1
                r["adopted"] += f["decision"] in ("accepted", "modified")
                r["rejected"] += f["decision"] == "rejected"
    # 黄金集：审核人最终认定的问题（采纳/修改/人工补充）对照预期标注
    gold = golden_cases()
    g_rows = []
    for d in docs:
        c = gold.get(d["golden_case_id"] or "")
        if not c:
            continue
        exp = {e["rule_id"] for e in c["expected"]}
        final = {f["rule_id"] for f in by_doc.get(d["id"], []) if f["decision"] in ("accepted", "modified")}
        ai_all = {f["rule_id"] for f in by_doc.get(d["id"], []) if f["source"] == "ai"}
        g_rows.append({"doc_id": d["id"], "case": c["id"], "arm": d["arm"], "expected": sorted(exp),
                       "final_missed": sorted(exp - final), "ai_missed": sorted(exp - ai_all),
                       "minutes": round(d["active_ms"] / 60000, 2)})
    return {"arms": arms, "per_rule": sorted(per_rule.values(), key=lambda r: -(r["ai"] + r["manual"])),
            "golden": g_rows, "reject_reason_labels": REJECT_REASONS}


# ---------- 黄金集演示 ----------
def golden_cases():
    p = ROOT / "golden" / "cases.jsonl"
    if not p.exists():
        return {}
    return {c["id"]: c for c in (json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip())}


@app.get("/api/golden")
def list_golden():
    return [{"id": c["id"], "doc_type": c.get("doc_type", "public"), "preview": c["text"][:60],
             "expected": [e["rule_id"] for e in c["expected"]]} for c in golden_cases().values()]


@app.get("/api/golden/{case_id}")
def get_golden(case_id: str):
    c = golden_cases().get(case_id)
    if not c:
        raise HTTPException(404, "样例不存在")
    return {"id": c["id"], "doc_type": c.get("doc_type", "public"), "text": c["text"]}


# ---------- 前端 ----------
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(APP_DIR / "static" / "index.html")
