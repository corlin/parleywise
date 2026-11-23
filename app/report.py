"""审查报告导出：HTML（可打印/存 PDF）与 Markdown。"""
import datetime, json, re, time
from pathlib import Path
from html import escape

SOURCES = json.loads((Path(__file__).resolve().parent.parent / "rules.json").read_text(encoding="utf-8")).get("sources", {})


def _t(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"


def _min(ms):
    return f"{(ms or 0) / 60000:.1f} 分钟"


def _split(doc):
    fs = doc["findings"]
    final = [f for f in fs if f["decision"] in ("accepted", "modified")]
    rejected = [f for f in fs if f["decision"] == "rejected"]
    pending = [f for f in fs if f["source"] == "ai" and not f["decision"]]
    return final, rejected, pending


def _src(f):
    return "人工补充" if f["source"] == "manual" else ("AI·已修改" if f["decision"] == "modified" else "AI·采纳")


def _opinion(f):
    return f["edited_suggestion"] if f["decision"] == "modified" else (f["suggestion"] or "按规则修改或删除")


def _marked_text(text, final):
    """原文中把认定问题的片段加 <mark> 并标编号；重叠片段合并成一段，编号并列。"""
    marks = sorted((sp[0], sp[1], i) for i, f in enumerate(final, 1) for sp in f["spans"][:1])
    merged = []
    for s, e, i in marks:
        if merged and s < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
            merged[-1][2].append(i)
        else:
            merged.append([s, e, [i]])
    out, pos = [], 0
    for s, e, nums in merged:
        out.append(escape(text[pos:s]))
        out.append(f"<mark>{escape(text[s:e])}</mark><sup>{','.join(map(str, sorted(nums)))}</sup>")
        pos = e
    out.append(escape(text[pos:]))
    return "".join(out).replace("\n", "<br>")


def _law(code):
    m = re.search(r"《[^》]+》", SOURCES.get(code, ""))
    return m.group(0) if m else (code or "")


def to_html(doc, reasons, conclusions):
    final, rejected, pending = _split(doc)
    rows = []
    for i, f in enumerate(final, 1):
        r = f["rule"]
        rows.append(f"""<tr><td>{i}</td><td><b>{escape(r['id'] or '')} {escape(r['title'] or '')}</b>
<div class="muted">{escape(r['article'] or '')}｜{escape(_law(r['source']))}</div></td>
<td>「{escape(f['quote'])}」</td><td>{escape(f['reason'] or '')}<div class="op">意见：{escape(_opinion(f))}</div>
{f'<div class="muted">备注：{escape(f["note"])}</div>' if f.get('note') else ''}</td><td>{_src(f)}</td></tr>""")
    rej = "".join(f"<li><b>{escape(f['rule_id'])} {escape(f['rule']['title'] or '')}</b>「{escape(f['quote'])}」"
                  f" — {escape(reasons.get(f['reject_reason'], ''))}{('：' + escape(f['note'])) if f.get('note') else ''}</li>"
                  for f in rejected)
    status = conclusions.get(doc.get("conclusion"), "未完成")
    warn = "" if doc["review_status"] == "completed" else \
        f'<p class="warn">审查尚未完成，本报告为草稿{f"（{len(pending)} 条 AI 意见未处理）" if pending else ""}。</p>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>合规审查报告 · {escape(doc['title'])}</title>
<style>
body{{font:14px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1b1f24;max-width:960px;margin:32px auto;padding:0 24px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #e3e6ea;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #e3e6ea;padding:6px 8px;vertical-align:top;text-align:left}}
th{{background:#f5f6f8;font-weight:600}} .muted{{color:#6b7280;font-size:12px}} .op{{margin-top:4px;color:#0f5132}}
mark{{background:#ffe8a3;padding:0 1px}} sup{{color:#b45309;font-weight:600}}
.meta td{{border:none;padding:2px 12px 2px 0}} .warn{{background:#fff4e5;border:1px solid #f5c27a;padding:8px 12px;border-radius:6px}}
.verdict{{display:inline-block;padding:2px 10px;border-radius:999px;background:#eef2ff;font-weight:600}}
.orig{{background:#fafafa;border:1px solid #e3e6ea;padding:12px 16px;border-radius:6px}}
.foot{{margin-top:32px;color:#6b7280;font-size:12px}}
@media print{{body{{margin:0}} .noprint{{display:none}}}}
</style></head><body>
<div class="noprint" style="text-align:right"><button onclick="print()">打印 / 存为 PDF</button></div>
<h1>宣传推介材料合规审查报告</h1>
<div class="muted">{escape(doc['title'])}</div>
{warn}
<table class="meta">
<tr><td>审查结论</td><td><span class="verdict">{escape(status)}</span></td></tr>
<tr><td>材料类型</td><td>{'公募基金' if doc['doc_type'] == 'public' else '私募基金'}</td></tr>
<tr><td>审核人</td><td>{escape(doc.get('reviewer') or '—')}</td></tr>
<tr><td>完成时间</td><td>{_t(doc.get('review_completed_at'))}</td></tr>
<tr><td>有效审查时长</td><td>{_min(doc.get('active_ms'))}</td></tr>
<tr><td>AI 初审</td><td>{escape(doc.get('model_info') or '—')}；AI 意见 {doc['ai_findings']} 条，采纳/修改 {sum(1 for f in final if f['source'] == 'ai')} 条，驳回 {len(rejected)} 条；人工补充 {doc['manual_findings']} 条</td></tr>
</table>
{f'<h2>审核人意见</h2><p>{escape(doc["summary_note"])}</p>' if doc.get('summary_note') else ''}
<h2>认定问题（{len(final)} 项）</h2>
{'<p>未认定问题。</p>' if not final else '<table><tr><th>#</th><th>规则</th><th>原文片段</th><th>理由与修改意见</th><th>来源</th></tr>' + ''.join(rows) + '</table>'}
<h2>原文（标注认定问题）</h2><div class="orig">{_marked_text(doc['text'], final)}</div>
{f'<h2>附录：已驳回的 AI 意见（{len(rejected)} 条）</h2><ul>{rej}</ul>' if rejected else ''}
<p class="foot">AI 意见仅为初审参考，最终结论以合规审核人判断为准。规则说明为条款转述，以监管原文为准。生成时间 {_t(time.time())}。</p>
</body></html>"""


def to_md(doc, reasons, conclusions):
    final, rejected, pending = _split(doc)
    L = [f"# 宣传推介材料合规审查报告：{doc['title']}", ""]
    if doc["review_status"] != "completed":
        L += ["> 审查尚未完成，本报告为草稿。", ""]
    L += [f"- 审查结论：{conclusions.get(doc.get('conclusion'), '未完成')}",
          f"- 审核人：{doc.get('reviewer') or '—'}",
          f"- 完成时间：{_t(doc.get('review_completed_at'))}",
          f"- 有效审查时长：{_min(doc.get('active_ms'))}",
          f"- AI 意见 {doc['ai_findings']} 条；驳回 {len(rejected)} 条；人工补充 {doc['manual_findings']} 条", ""]
    if doc.get("summary_note"):
        L += ["## 审核人意见", "", doc["summary_note"], ""]
    L += [f"## 认定问题（{len(final)} 项）", ""]
    for i, f in enumerate(final, 1):
        r = f["rule"]
        L += [f"{i}. **{r['id']} {r['title']}**（{_law(r['source'])}{r['article']}，{_src(f)}）",
              f"    - 原文片段：「{f['quote']}」",
              f"    - 理由：{f['reason'] or '—'}",
              f"    - 意见：{_opinion(f)}"]
        if f.get("note"):
            L.append(f"    - 备注：{f['note']}")
    if rejected:
        L += ["", f"## 附录：已驳回的 AI 意见（{len(rejected)} 条）", ""]
        L += [f"- {f['rule_id']} {f['rule']['title']}「{f['quote']}」— {reasons.get(f['reject_reason'], '')}"
              + (f"：{f['note']}" if f.get("note") else "") for f in rejected]
    L += ["", "## 原文", "", doc["text"], "", "> AI 意见仅为初审参考，最终结论以合规审核人判断为准。"]
    return "\n".join(L) + "\n"
