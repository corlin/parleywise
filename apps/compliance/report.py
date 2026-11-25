"""合规初审片段聚类与报告格式化。"""
import re

from .rules import SEVERITY_RANK


def _span(q, text):
    """片段在原文中首次出现的位置（忽略空白差异）；找不到返回 None。"""
    core = re.sub(r"\s+", "", q or "")
    if not core:
        return None
    m = re.search(r"\s*".join(map(re.escape, core)), text)
    return (m.start(), m.end()) if m else None


def group_snippets(kept, text, rules):
    """把同一段原文（片段相同、互相包含或位置重叠）的多条规则意见合并为一处片段。
    每组内主规则在前：严重度高者优先，其次模型意见优先，再按置信度。"""
    n = len(kept)
    quotes = [[f["quote"]] + list(f.get("also_quotes", [])) for f in kept]
    spans = [[s for s in (_span(q, text) for q in qs) if s] for qs in quotes]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def linked(i, j):
        if any(a[0] < b[1] and b[0] < a[1] for a in spans[i] for b in spans[j]):
            return True
        return any(qa and qb and (qa in qb or qb in qa) for qa in quotes[i] for qb in quotes[j])

    for i in range(n):
        for j in range(i + 1, n):
            if linked(i, j):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    def prio(i):
        f = kept[i]
        return (SEVERITY_RANK.get(rules[f["rule_id"]].get("severity"), 3),
                f.get("stage") != "llm", -float(f.get("confidence") or 0))

    out = []
    for idx in groups.values():
        idx.sort(key=prio)
        start = min((s[0] for i in idx for s in spans[i]), default=len(text))
        out.append((start, [kept[i] for i in idx]))
    return [g for _, g in sorted(out, key=lambda x: x[0])]


def snippet_quotes(group):
    """一处片段展示的原文：去掉被其他片段包含的短片段。"""
    qs = []
    for f in group:
        for q in [f["quote"]] + list(f.get("also_quotes", [])):
            if q and q not in qs:
                qs.append(q)
    return [q for q in qs if not any(q != o and q in o for o in qs)]


def to_markdown(doc_id, text, kept, dropped, rules):
    groups = group_snippets(kept, text, rules)
    lines = [f"# 合规初审报告：{doc_id}", "", "> 本报告为 AI 初审意见，须由合规人员终审。", "",
             "## 原文", "", text, "",
             f"## 疑似问题（{len(groups)} 处片段，涉及 {len(kept)} 条规则意见）", ""]
    if not groups:
        lines.append("未发现疑似问题。")
    for i, g in enumerate(groups, 1):
        head = rules[g[0]["rule_id"]]
        title = f"{head['id']} {head['title']}"
        if len(g) > 1:
            title += "｜另涉：" + "、".join(f"{rules[f['rule_id']]['id']} {rules[f['rule_id']]['title']}" for f in g[1:])
        lines += [f"{i}. **{title}**",
                  "    - 原文片段：" + "、".join(f"「{q}」" for q in snippet_quotes(g))]
        for f in g:
            r = rules[f["rule_id"]]
            lines += [f"    - {r['id']}（{r['article']}，{f.get('stage')}）理由：{f.get('reason', '')}",
                      f"      建议：{f.get('suggestion') or '按规则修改或删除'}"]
    if dropped:
        lines += ["", f"## 被校验器拦下的意见（{len(dropped)} 条，不输出给审核人）", ""]
        lines += [f"- {d.get('rule_id')}：{d['drop_reason']}（片段：{d.get('quote')}）" for d in dropped]
    return "\n".join(lines) + "\n"


def to_record(doc_id, text, doc_type, kept, dropped, rules):
    """每篇的结构化结果，便于以后不调模型直接重算指标。"""
    return {"id": doc_id, "doc_type": doc_type, "kept": kept, "dropped": dropped,
            "snippets": [[f["rule_id"] for f in g] for g in group_snippets(kept, text, rules)]}
