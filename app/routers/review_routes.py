"""合规材料审查、复核、报告导出、度量与黄金集路由。"""
import io
import json
import statistics
import time
import traceback
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from .. import deps, model_calls, report
from ..db import db, log_event
from ..deps import (
    CHEAP,
    CONCLUSIONS,
    MAX_CHARS,
    MAX_UPLOAD,
    REJECT_REASONS,
    ROOT,
    RULES,
    STRONG,
    _int,
    _s,
    me,
    review,
)

router = APIRouter()
EXECUTOR = ThreadPoolExecutor(max_workers=2)


class StageLLM(review.LLM):
    """第 1 次调用是初筛，之后是复核；调用前把阶段写库，供前端显示。"""

    def __init__(self, doc_id):
        self.run_id = uuid.uuid4().hex
        super().__init__(observer=lambda event: model_calls.record(
            event, kind="review", document_id=doc_id, stage=self.stage, run_id=self.run_id
        ))
        self.doc_id, self.n = doc_id, 0
        self.stage = None

    def chat(self, model, system, user):
        self.n += 1
        self.stage = "triage" if self.n == 1 else "review"
        set_doc(self.doc_id, stage=self.stage)
        return super().chat(model, system, user)


def set_doc(doc_id, **kw):
    with db() as con:
        con.execute(f"UPDATE documents SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), doc_id))


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def run_pipeline(doc_id):
    with db() as con:
        d = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not d:
        return
    t0 = time.time()
    set_doc(doc_id, status="running", error=None)
    try:
        llm = StageLLM(doc_id) if d["mode"] == "full" else None
        if llm and not llm.key:
            raise RuntimeError("未设置 LITELLM_API_KEY（mvp0/.env）")
        text = d["text"]
        collab = bool(d["collaboration"]) if "collaboration" in d.keys() else False
        kept, dropped = review.review_doc(
            text, d["doc_type"], RULES, llm, CHEAP, STRONG,
            on_stage=lambda s: set_doc(doc_id, stage=s),
            collaboration=collab,
        )
        secs = round(time.time() - t0, 1)
        with db() as con:
            con.execute("DELETE FROM findings WHERE doc_id=? AND source='ai'", (doc_id,))
            con.execute("DELETE FROM dropped WHERE doc_id=?", (doc_id,))
            for gi, g in enumerate(review.group_snippets(kept, text, RULES)):
                for ri, f in enumerate(g):
                    qs = [f["quote"]] + list(f.get("also_quotes", []))
                    con.execute(
                        "INSERT INTO findings(doc_id, source, group_idx, rank_in_group, rule_id, quote, also_quotes,"
                        " spans, reason, suggestion, confidence, stage, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            doc_id, "ai", gi, ri, f["rule_id"], f["quote"],
                            json.dumps(qs[1:], ensure_ascii=False),
                            json.dumps([s for s in (review._span(q, text) for q in qs) if s]),
                            str(f.get("reason") or ""), str(f.get("suggestion") or ""),
                            _num(f.get("confidence")), f.get("stage"), time.time(),
                        ),
                    )
            con.executemany(
                "INSERT INTO dropped(doc_id, payload) VALUES (?,?)",
                [(doc_id, json.dumps(x, ensure_ascii=False)) for x in dropped],
            )
            model_desc = f"{CHEAP} → {STRONG}" + ("（多角色）" if collab else "") if llm else "仅关键词"
            con.execute(
                "UPDATE documents SET status='done', stage=NULL, pipeline_seconds=?, usage_json=?, model_info=?, run_id=? WHERE id=?",
                (secs, json.dumps(llm.usage) if llm else None, model_desc, llm.run_id if llm else None, doc_id),
            )
            log_event(con, doc_id, "pipeline_done", {"findings": len(kept), "seconds": secs})
    except Exception as e:  # 失败写库，前端可重试
        traceback.print_exc()
        set_doc(doc_id, status="failed", stage=None, error=str(e)[:500], pipeline_seconds=round(time.time() - t0, 1))


def enqueue(doc_id):
    set_doc(doc_id, status="queued", stage=None, error=None)
    EXECUTOR.submit(run_pipeline, doc_id)


def requeue_interrupted():
    """服务重启时，把中断的任务重新排队。"""
    with db() as _c:
        stuck = [r[0] for r in _c.execute("SELECT id FROM documents WHERE status IN ('queued','running')")]
    for doc_id in stuck:
        enqueue(doc_id)


# PDF 字体常把汉字映射成部首字符（如 ⻛ ⾦），会让关键词和片段匹配失败
_RADICALS = dict(zip("⻛⻔⻢⻓⻋⻅⻆⻝⻘⻜⻄⻩⻰⻮⻉⻈⻨⻥⻚⻧⻦⻤⻖⻗⻙", "风门马长车见角食青飞西黄龙齿贝讠麦鱼页卤镸鬼阝雨纟"))


def fix_pdf_radicals(t):
    return "".join(unicodedata.normalize("NFKC", c) if 0x2F00 <= ord(c) <= 0x2FDF else _RADICALS.get(c, c) for c in t)


def extract_text(filename, data):
    name = (filename or "").lower()
    try:
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
            parts += [" | ".join(c.text for c in row.cells) for t in d.tables for row in t.rows]
            return "\n".join(parts)
        if name.endswith(".pdf"):
            from pypdf import PdfReader
            txt = fix_pdf_radicals("\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages))
            if not txt.strip():
                raise HTTPException(400, "PDF 里没有可提取的文字，可能是扫描件；暂不支持 OCR")
            return txt
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"文件无法解析：{type(e).__name__}") from e
    raise HTTPException(400, "仅支持 .txt / .md / .docx / .pdf（旧版 .doc 请另存为 .docx）")


def clean_text(t):
    out = "\n".join(l.rstrip() for l in t.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out


def finding_dict(r):
    f = dict(r)
    f["also_quotes"] = json.loads(f["also_quotes"] or "[]")
    f["spans"] = json.loads(f["spans"] or "[]")
    rule = RULES.get(f["rule_id"], {})
    f["rule"] = {k: rule.get(k) for k in ("id", "title", "article", "source", "description", "severity")}
    return f


def doc_summary(con, d):
    c = con.execute(
        """SELECT
        SUM(source='ai') ai, SUM(source='ai' AND decision IS NOT NULL) ai_decided,
        SUM(source='manual') manual, COUNT(DISTINCT CASE WHEN source='ai' THEN group_idx END) groups
        FROM findings WHERE doc_id=?""",
        (d["id"],),
    ).fetchone()
    s = {k: d[k] for k in ("id", "title", "doc_type", "arm", "mode", "status", "stage", "error", "created_at",
                           "created_by", "review_status", "active_ms", "conclusion", "reviewer")}
    s["collaboration"] = bool(d["collaboration"]) if "collaboration" in d.keys() else False
    s["run_id"] = d["run_id"] if "run_id" in d.keys() else None
    usage = json.loads(d["usage_json"]) if d["usage_json"] else None
    s["usage"] = usage
    s.update(ai_findings=c["ai"] or 0, ai_decided=c["ai_decided"] or 0, manual_findings=c["manual"] or 0,
             snippets=c["groups"] or 0)
    if d["arm"] == "control" and d["review_status"] != "completed":
        s.update(ai_findings=0, ai_decided=0, snippets=0)
    return s


def load_doc(con, doc_id):
    d = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "材料不存在")
    return d


def load_finding(con, fid):
    f = con.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone()
    if not f:
        raise HTTPException(404, "意见不存在")
    return f


def _editable(d):
    if d["review_status"] == "completed":
        raise HTTPException(409, "审查已完成，如需修改请先重新打开")
    if d["status"] != "done":
        raise HTTPException(409, "AI 初审尚未完成")


def golden_cases():
    out = {}
    for filename, dataset in (("cases.jsonl", "synthetic"), ("real_cases.jsonl", "real")):
        p = ROOT / "golden" / filename
        if not p.exists():
            continue
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                c = json.loads(l)
                c.setdefault("dataset", dataset)
                out[c["id"]] = c
    return out


@router.get("/api/documents")
def list_documents():
    with db() as con:
        return [doc_summary(con, d) for d in con.execute("SELECT * FROM documents ORDER BY id DESC")]


@router.post("/api/documents")
async def create_document(
    request: Request,
    title: str = Form(""),
    doc_type: str = Form("public"),
    text: str = Form(""),
    arm: str = Form("ai"),
    mode: str = Form("full"),
    collaboration: bool = Form(False),
    golden_case_id: str = Form(""),
    file: UploadFile | None = File(None),
):
    fname = None
    if file is not None and file.filename:
        fname = file.filename
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(400, "文件超过 10 MB")
        text = extract_text(fname, data)
    text = clean_text(text or "")
    if not text:
        raise HTTPException(400, "材料内容为空")
    if len(text) > MAX_CHARS:
        raise HTTPException(400, f"材料超过 {MAX_CHARS} 字，请拆分后提交")
    if doc_type not in ("public", "private") or arm not in ("ai", "control") or mode not in ("full", "offline"):
        raise HTTPException(400, "参数不合法")
    if golden_case_id not in golden_cases():
        golden_case_id = ""
    use_collab = 1 if (collaboration and mode == "full") else 0
    title = title.strip()[:80] or (Path(fname).stem if fname else text[:24].replace("\n", " "))
    who = me(request)["name"]
    with db() as con:
        cur = con.execute(
            "INSERT INTO documents(title, doc_type, text, source_filename, golden_case_id, arm, mode, collaboration,"
            " created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (title, doc_type, text, fname, golden_case_id or None, arm, mode, use_collab, time.time(), who),
        )
        doc_id = cur.lastrowid
        log_event(con, doc_id, "created", {"arm": arm, "mode": mode, "collaboration": bool(use_collab), "file": fname},
                  reviewer=who)
    enqueue(doc_id)
    return {"id": doc_id}


def document_view(doc_id):
    with db() as con:
        d = load_doc(con, doc_id)
        out = doc_summary(con, d)
        out.update(text=d["text"], summary_note=d["summary_note"], model_info=d["model_info"],
                   review_completed_at=d["review_completed_at"])
        rows = con.execute("SELECT * FROM findings WHERE doc_id=? ORDER BY group_idx, rank_in_group, id", (doc_id,))
        hide_ai = d["arm"] == "control" and d["review_status"] != "completed"
        out["ai_hidden"] = hide_ai
        out["findings"] = [finding_dict(r) for r in rows if not (hide_ai and r["source"] == "ai")]
        if d["golden_case_id"] and d["review_status"] == "completed":
            out["golden"] = golden_cases().get(d["golden_case_id"])
    return out


@router.get("/api/documents/{doc_id}")
def get_document(doc_id: int):
    return document_view(doc_id)


@router.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int, request: Request):
    if me(request)["role"] != "admin":
        raise HTTPException(403, "只有管理员可以删除材料")
    with db() as con:
        load_doc(con, doc_id)
        con.execute("DELETE FROM dropped WHERE doc_id=?", (doc_id,))
        con.execute("DELETE FROM model_calls WHERE document_id=? AND kind='review'", (doc_id,))
        con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        log_event(con, doc_id, "deleted", reviewer=me(request)["name"])
    return {"ok": True}


@router.post("/api/documents/{doc_id}/retry")
def retry(doc_id: int):
    with db() as con:
        if load_doc(con, doc_id)["status"] != "failed":
            raise HTTPException(409, "只有失败的初审可以重试")
    enqueue(doc_id)
    return {"ok": True}


@router.post("/api/documents/{doc_id}/heartbeat")
async def heartbeat(doc_id: int, request: Request):
    body = await request.json()
    delta = max(0, min(_int(body.get("active_ms")), 30000))
    with db() as con:
        d = load_doc(con, doc_id)
        if d["review_status"] == "completed" or d["status"] != "done":
            return {"active_ms": d["active_ms"]}
        con.execute(
            "UPDATE documents SET active_ms=active_ms+?, review_status='in_progress',"
            " review_started_at=COALESCE(review_started_at, ?), reviewer=COALESCE(reviewer, ?) WHERE id=?",
            (delta, time.time(), me(request)["name"], doc_id),
        )
        return {"active_ms": d["active_ms"] + delta}


@router.patch("/api/findings/{fid}")
async def decide(fid: int, request: Request):
    body = await request.json()
    decision, rr = body.get("decision"), body.get("reject_reason")
    note, edited = _s(body.get("note")), _s(body.get("edited_suggestion"))
    if decision not in ("accepted", "modified", "rejected", None):
        raise HTTPException(400, "decision 不合法")
    if decision == "rejected" and rr not in REJECT_REASONS:
        raise HTTPException(400, "驳回须选择原因")
    if decision == "rejected" and rr == "other" and not note:
        raise HTTPException(400, "选择“其他”时请填写说明")
    if decision == "modified" and not edited:
        raise HTTPException(400, "修改时请填写修改后的意见")
    with db() as con:
        f = load_finding(con, fid)
        if f["source"] != "ai":
            raise HTTPException(400, "人工补充的问题不需要处理")
        _editable(load_doc(con, f["doc_id"]))
        con.execute(
            "UPDATE findings SET decision=?, reject_reason=?, note=?, edited_suggestion=?, decided_at=?,"
            " elapsed_ms=COALESCE(elapsed_ms,0)+? WHERE id=?",
            (
                decision, rr if decision == "rejected" else None, note or None,
                edited if decision == "modified" else None, time.time() if decision else None,
                max(0, _int(body.get("elapsed_ms"))), fid,
            ),
        )
        log_event(
            con, f["doc_id"], "decision",
            {"decision": decision, "reject_reason": rr, "note": note, "edited_suggestion": edited,
             "elapsed_ms": body.get("elapsed_ms")},
            finding_id=fid, reviewer=me(request)["name"],
        )
        return finding_dict(load_finding(con, fid))


@router.post("/api/documents/{doc_id}/findings")
async def add_manual(doc_id: int, request: Request):
    body = await request.json()
    with db() as con:
        d = load_doc(con, doc_id)
        _editable(d)
        rid = body.get("rule_id")
        if rid not in RULES:
            raise HTTPException(400, "请选择规则")
        s, e = _int(body.get("start"), -1), _int(body.get("end"), -1)
        if not (0 <= s < e <= len(d["text"])):
            raise HTTPException(400, "请先在原文中划选片段")
        quote_ = d["text"][s:e]
        top = con.execute("SELECT MAX(group_idx) FROM findings WHERE doc_id=?", (doc_id,)).fetchone()[0] or 0
        cur = con.execute(
            "INSERT INTO findings(doc_id, source, group_idx, rule_id, quote, spans, reason, suggestion, decision,"
            " decided_at, elapsed_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc_id, "manual", max(1000, top + 1), rid, quote_, json.dumps([[s, e]]),
                _s(body.get("reason")), _s(body.get("suggestion")), "accepted", time.time(),
                max(0, _int(body.get("elapsed_ms"))), time.time(),
            ),
        )
        log_event(con, doc_id, "manual_add", {"rule_id": rid, "quote": quote_},
                  finding_id=cur.lastrowid, reviewer=me(request)["name"])
        return finding_dict(load_finding(con, cur.lastrowid))


@router.delete("/api/findings/{fid}")
def delete_manual(fid: int, request: Request):
    with db() as con:
        f = load_finding(con, fid)
        if f["source"] != "manual":
            raise HTTPException(400, "只能删除人工补充的问题")
        _editable(load_doc(con, f["doc_id"]))
        con.execute("DELETE FROM findings WHERE id=?", (fid,))
        log_event(con, f["doc_id"], "manual_delete", {"rule_id": f["rule_id"]},
                  finding_id=fid, reviewer=me(request)["name"])
    return {"ok": True}


@router.post("/api/documents/{doc_id}/complete")
async def complete(doc_id: int, request: Request):
    body = await request.json()
    if body.get("conclusion") not in CONCLUSIONS:
        raise HTTPException(400, "请选择审查结论")
    who = me(request)["name"]
    with db() as con:
        d = load_doc(con, doc_id)
        _editable(d)
        left = con.execute("SELECT COUNT(*) FROM findings WHERE doc_id=? AND source='ai' AND decision IS NULL",
                           (doc_id,)).fetchone()[0]
        if d["arm"] == "ai" and left:
            raise HTTPException(400, f"还有 {left} 条 AI 意见未处理")
        now = time.time()
        con.execute(
            "UPDATE documents SET review_status='completed', review_completed_at=?, conclusion=?,"
            " summary_note=?, reviewer=?, review_started_at=COALESCE(review_started_at, ?) WHERE id=?",
            (now, body["conclusion"], _s(body.get("summary_note")) or None, who, now, doc_id),
        )
        log_event(con, doc_id, "completed", {"conclusion": body["conclusion"], "active_ms": d["active_ms"]},
                  reviewer=who)
    return {"ok": True}


@router.post("/api/documents/{doc_id}/reopen")
def reopen(doc_id: int, request: Request):
    with db() as con:
        d = load_doc(con, doc_id)
        if d["review_status"] != "completed":
            raise HTTPException(409, "审查尚未完成")
        if d["arm"] == "control":
            raise HTTPException(409, "对照组完成后已看过 AI 意见，不能重新打开，否则计时和结果会被污染")
        con.execute("UPDATE documents SET review_status='in_progress', review_completed_at=NULL WHERE id=?", (doc_id,))
        log_event(con, doc_id, "reopened", reviewer=me(request)["name"])
    return {"ok": True}


@router.get("/api/documents/{doc_id}/report.{fmt}")
def export_report(doc_id: int, fmt: str):
    data = document_view(doc_id)
    disp = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'审查报告_{doc_id}')}.{fmt}"}
    if fmt == "html":
        return HTMLResponse(report.to_html(data, REJECT_REASONS, CONCLUSIONS))
    if fmt == "md":
        return PlainTextResponse(report.to_md(data, REJECT_REASONS, CONCLUSIONS), media_type="text/markdown", headers=disp)
    if fmt == "json":
        with db() as con:
            data["events"] = [dict(r) for r in con.execute(
                "SELECT type, payload, reviewer, ts FROM events WHERE doc_id=? ORDER BY id", (doc_id,))]
        return JSONResponse(data, headers=disp)
    raise HTTPException(404)


@router.get("/api/metrics")
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
        mins = sorted(d["active_ms"] / 60000 for d in ds)
        ai = [f for d in ds for f in by_doc.get(d["id"], []) if f["source"] == "ai"]
        manual = [f for d in ds for f in by_doc.get(d["id"], []) if f["source"] == "manual"]
        a = {
            "docs": len(ds),
            "avg_minutes": round(sum(mins) / len(mins), 2) if mins else None,
            "median_minutes": round(statistics.median(mins), 2) if mins else None,
            "manual_per_doc": round(len(manual) / len(ds), 2) if ds else None,
        }
        if arm == "ai":
            dec = {k: sum(f["decision"] == k for f in ai) for k in ("accepted", "modified", "rejected")}
            reasons = {}
            for f in ai:
                if f["decision"] == "rejected":
                    reasons[f["reject_reason"]] = reasons.get(f["reject_reason"], 0) + 1
            total = sum(dec.values())
            usages = [json.loads(d["usage_json"]) for d in ds if d.get("usage_json")]
            known_costs = [u["cost_usd"] for u in usages if u and not u.get("calls_without_cost")]
            toks = [(u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0) for u in usages if u]
            a.update(
                ai_findings=len(ai), decisions=dec, reject_reasons=reasons,
                adoption_rate=round((dec["accepted"] + dec["modified"]) / total, 3) if total else None,
                avg_tokens=round(sum(toks) / len(toks)) if toks else None,
                avg_cost_usd=round(sum(known_costs) / len(known_costs), 5) if known_costs else None,
                known_cost_docs=len(known_costs),
            )
        else:
            human = {(f["doc_id"], f["rule_id"]) for f in manual}
            aiset = {(f["doc_id"], f["rule_id"]) for f in ai}
            a.update(human_issue_rules=len(human), ai_also_found=len(human & aiset), ai_only=len(aiset - human))
        arms[arm] = a
    per_rule = {}
    for d in docs:
        if d["arm"] != "ai":
            continue
        for f in by_doc.get(d["id"], []):
            r = per_rule.setdefault(
                f["rule_id"],
                {"rule_id": f["rule_id"], "title": RULES.get(f["rule_id"], {}).get("title"),
                 "ai": 0, "adopted": 0, "rejected": 0, "manual": 0},
            )
            if f["source"] == "manual":
                r["manual"] += 1
            else:
                r["ai"] += 1
                r["adopted"] += f["decision"] in ("accepted", "modified")
                r["rejected"] += f["decision"] == "rejected"
    gold, g_rows = golden_cases(), []
    for d in docs:
        c = gold.get(d["golden_case_id"] or "")
        if not c:
            continue
        exp = {e["rule_id"] for e in c["expected"]}
        final = {f["rule_id"] for f in by_doc.get(d["id"], []) if f["decision"] in ("accepted", "modified")}
        ai_all = {f["rule_id"] for f in by_doc.get(d["id"], []) if f["source"] == "ai"}
        g_rows.append({
            "doc_id": d["id"], "case": c["id"], "arm": d["arm"], "expected": sorted(exp),
            "final_missed": sorted(exp - final), "ai_missed": sorted(exp - ai_all),
            "minutes": round(d["active_ms"] / 60000, 2),
        })
    return {
        "arms": arms,
        "per_rule": sorted(per_rule.values(), key=lambda r: -(r["ai"] + r["manual"])),
        "golden": g_rows,
    }


@router.get("/api/golden")
def list_golden():
    return [{"id": c["id"], "doc_type": c.get("doc_type", "public"), "dataset": c.get("dataset", "synthetic"),
             "text": c["text"]} for c in golden_cases().values()]
