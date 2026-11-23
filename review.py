#!/usr/bin/env python3
"""hiagent MVP0：基金营销材料合规初审脚本。

流程：关键词初筛 -> 小模型逐条初筛（召回优先）-> 强模型复核 -> 校验器（原文片段必须逐字存在、
条款必须在规则库中）-> 报告。只用 Python 标准库；模型经 LiteLLM 网关（OpenAI 兼容接口）调用。

用法（Python 环境用 uv 管理）：
  uv run review.py --text-file 文案.txt                 # 审一份材料，输出 Markdown 报告
  uv run review.py --golden golden/cases.jsonl --eval   # 在黄金集上跑评测
  加 --offline 只跑关键词初筛（不调模型），用于离线基线。

环境变量（可写在同目录 .env，已加入 .gitignore；模板见 .env.example）：
  LITELLM_BASE_URL   默认 https://llm.corlin.cn
  LITELLM_API_KEY    网关 key（只放环境变量，不要写进代码或提交到仓库）
  HIAGENT_MODEL_CHEAP / HIAGENT_MODEL_STRONG  网关里的模型别名，默认 cheap / strong
"""
import argparse, datetime, http.client, json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEGATION = re.compile(r"(不|并不|不能|无法|不得|未|不构成|不表明)[^，。；！？]{0,40}$")


def load_dotenv(path=HERE / ".env"):
    """读取同目录 .env（不覆盖已存在的环境变量）。.env 已在 .gitignore 中。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_rules(path=HERE / "rules.json"):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {r["id"]: r for r in data["rules"]}


def applicable(rules, doc_type):
    return {k: r for k, r in rules.items()
            if r["check"] != "manual" and r.get("applies_to", doc_type) == doc_type}


# ---------- 1. 关键词初筛 ----------
def keyword_scan(text, rules):
    hits = []
    for r in rules.values():
        for kw in r.get("keywords", []):
            for m in re.finditer(re.escape(kw), text):
                before = text[max(0, m.start() - 48):m.start()]
                if NEGATION.search(before):
                    continue  # 如“不构成……保证”“不能保证盈利”属于免责表述
                hits.append({"rule_id": r["id"], "quote": kw, "stage": "keyword",
                             "reason": f"命中关键词「{kw}」", "suggestion": "", "confidence": 0.5})
                break
    return hits


# ---------- 2/3. 模型调用 ----------
class LLM:
    def __init__(self):
        self.base = os.environ.get("LITELLM_BASE_URL", "https://llm.corlin.cn").rstrip("/")
        self.key = os.environ.get("LITELLM_API_KEY", "")
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}

    def chat(self, model, system, user):
        body = json.dumps({"model": model, "temperature": 0,
                           "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(self.base + "/v1/chat/completions", data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.key}"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = json.loads(resp.read())
                    cost = resp.headers.get("x-litellm-response-cost")
                break
            except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError) as e:
                if attempt == 2:
                    raise RuntimeError(f"网关调用失败：{e}") from e
                time.sleep(2 * (attempt + 1))
        u = payload.get("usage") or {}
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += u.get("prompt_tokens", 0)
        self.usage["completion_tokens"] += u.get("completion_tokens", 0)
        if cost:
            try:
                self.usage["cost_usd"] += float(cost)
            except ValueError:
                pass
        content = payload["choices"][0]["message"]["content"]
        return parse_json(content)


def parse_json(content):
    content = content.strip()
    m = re.search(r"\{.*\}", content, re.S)
    try:
        return json.loads(m.group(0) if m else content)
    except json.JSONDecodeError:
        return {"findings": []}


def today():
    return os.environ.get("HIAGENT_TODAY") or datetime.date.today().isoformat()


def rules_block(rules):
    return "\n".join(f"{r['id']}｜{r['title']}：{r['description']}" for r in rules.values())


TRIAGE_SYS = ("你是基金营销材料合规初审助手。逐条对照给定规则检查材料，宁可多报不可漏报。"
              "只输出 JSON：{\"findings\":[{\"rule_id\":\"Rxx\",\"quote\":\"材料中的原文片段，必须逐字复制\","
              "\"reason\":\"为什么可能违规\",\"suggestion\":\"修改建议\",\"confidence\":0到1}]}。"
              "quote 必须是材料原文的连续子串，不得改写；没有问题时返回空数组。")

REVIEW_SYS = ("你是资深基金合规审核员，负责复核初审意见。对每条初审意见判断是否真的违反所引规则："
              "材料中已有合规声明或免责表述的，不算违规；规则要求“缺少某项”的，须确认全文（含文末声明）确实没有；"
              "同一片段可以同时违反多条规则：逐条判断每条规则自身的要求是否被违反，各自成立就各自保留，"
              "不要因为已归入一条规则就放弃另一条（例如“缺少某项必备内容”与“表述本身不当”是不同要求）；"
              "只有两条意见针对的是同一项要求、仅措辞不同的，才合并为最贴切的一条。"
              "保留标准要严：只保留能指出具体违规事实、无需额外假设就成立的意见；"
              "措辞中性、仅“可能”引起误解或属于风格建议的，删除；"
              "文末的数据来源、风险提示等声明适用于全文，不要以“该声明覆盖不到某处”为由另报。"
              "只输出 JSON：{\"findings\":[...保留的意见，字段同输入，可修改 reason/suggestion/confidence...]}。"
              "quote 必须保持为材料原文的连续子串。")


def llm_review(text, rules, hints, llm, cheap, strong):
    user = (f"【今天日期】{today()}（判断成立时间、展示期间是否满 6 个月等，以此为准）\n\n"
            f"【规则】\n{rules_block(rules)}\n\n【关键词提示（仅供参考）】\n"
            + ("、".join(sorted({h['rule_id'] for h in hints})) or "无")
            + f"\n\n【材料】\n{text}")
    triage = llm.chat(cheap, TRIAGE_SYS, user).get("findings", [])
    if not triage:
        return [], []
    user2 = (f"【今天日期】{today()}\n\n【规则】\n{rules_block(rules)}\n\n【材料】\n{text}\n\n【初审意见】\n"
             + json.dumps(triage, ensure_ascii=False))
    reviewed = llm.chat(strong, REVIEW_SYS, user2).get("findings", [])
    for f in reviewed:
        f["stage"] = "llm"
    # 记录复核未保留的初审意见，便于排查漏检（只进报告的“被拦下”一节，不计入结果）
    kept_rules = {f.get("rule_id") for f in reviewed}
    rejected = [{**t, "drop_reason": "复核未保留"} for t in triage if t.get("rule_id") not in kept_rules]
    return reviewed, rejected


# ---------- 4. 校验器 ----------
def verify(findings, text, rules):
    kept, dropped = [], []
    norm = re.sub(r"\s+", "", text)
    for f in findings:
        rid, q = f.get("rule_id"), (f.get("quote") or "").strip()
        if rid not in rules:
            dropped.append({**f, "drop_reason": "规则编号不在规则库或不适用"})
        elif not q or (q not in text and re.sub(r"\s+", "", q) not in norm):
            dropped.append({**f, "drop_reason": "引用片段不在原文中"})
        else:
            kept.append(f)
    # 去重：每条规则只出一条意见，模型意见优先，其余片段并入 also_quotes
    by_rule = {}
    for f in sorted(kept, key=lambda x: x.get("stage") != "llm"):
        if f["rule_id"] not in by_rule:
            by_rule[f["rule_id"]] = {**f, "also_quotes": []}
        elif f["quote"] not in by_rule[f["rule_id"]]["quote"]:
            by_rule[f["rule_id"]]["also_quotes"].append(f["quote"])
    return list(by_rule.values()), dropped


def review_doc(text, doc_type, all_rules, llm=None, cheap="cheap", strong="strong"):
    rules = applicable(all_rules, doc_type)
    hints = keyword_scan(text, rules)
    findings = [h for h in hints if rules[h["rule_id"]]["check"] == "keyword"]
    rejected = []
    if llm is not None:
        reviewed, rejected = llm_review(text, rules, hints, llm, cheap, strong)
        findings += reviewed
    kept, dropped = verify(findings, text, rules)
    return kept, dropped + rejected


# ---------- 报告与评测 ----------
def to_markdown(doc_id, text, kept, dropped, rules):
    lines = [f"# 合规初审报告：{doc_id}", "", "> 本报告为 AI 初审意见，须由合规人员终审。", "",
             "## 原文", "", text, "", f"## 疑似问题（{len(kept)} 条）", ""]
    if not kept:
        lines.append("未发现疑似问题。")
    for i, f in enumerate(kept, 1):
        r = rules[f["rule_id"]]
        lines += [f"{i}. **{r['id']} {r['title']}**（{r['article']}，{f.get('stage')}）",
                  f"    - 原文片段：「{f['quote']}」" + ("".join(f"、「{q}」" for q in f.get("also_quotes", []))),
                  f"    - 理由：{f.get('reason', '')}",
                  f"    - 建议：{f.get('suggestion') or '按规则修改或删除'}"]
    if dropped:
        lines += ["", f"## 被校验器拦下的意见（{len(dropped)} 条，不输出给审核人）", ""]
        lines += [f"- {d.get('rule_id')}：{d['drop_reason']}（片段：{d.get('quote')}）" for d in dropped]
    return "\n".join(lines) + "\n"


def evaluate(results):
    """strict：只认 expected；lenient：命中 acceptable（可接受的次要命中）不算误报。"""
    tp = fp = fp_strict = fn = 0
    per_rule_miss, per_rule_fp = {}, {}
    for case, kept in results:
        exp = {e["rule_id"] for e in case["expected"]}
        ok = set(case.get("acceptable", []))
        got = {f["rule_id"] for f in kept}
        tp += len(exp & got); fn += len(exp - got)
        fp_strict += len(got - exp); fp += len(got - exp - ok)
        for r in exp - got:
            per_rule_miss.setdefault(r, []).append(case["id"])
        for r in got - exp - ok:
            per_rule_fp.setdefault(r, []).append(case["id"])
    r3 = lambda a, b: round(a / b, 3) if b else 1.0
    return {"tp": tp, "fn": fn, "recall": r3(tp, tp + fn),
            "fp_strict": fp_strict, "precision_strict": r3(tp, tp + fp_strict),
            "fp_lenient": fp, "precision_lenient": r3(tp, tp + fp),
            "missed": per_rule_miss, "false_positives_lenient": per_rule_fp}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file")
    ap.add_argument("--doc-type", default="public", choices=["public", "private"])
    ap.add_argument("--golden")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default=str(HERE / "out"))
    a = ap.parse_args()
    load_dotenv()

    rules = load_rules()
    llm = None if a.offline else LLM()
    if llm is not None and not llm.key:
        sys.exit("未设置 LITELLM_API_KEY；如只想跑关键词基线，请加 --offline")
    cheap = os.environ.get("HIAGENT_MODEL_CHEAP", "cheap")
    strong = os.environ.get("HIAGENT_MODEL_STRONG", "strong")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    if a.text_file:
        text = Path(a.text_file).read_text(encoding="utf-8").strip()
        kept, dropped = review_doc(text, a.doc_type, rules, llm, cheap, strong)
        md = to_markdown(Path(a.text_file).stem, text, kept, dropped, rules)
        (out / (Path(a.text_file).stem + ".md")).write_text(md, encoding="utf-8")
        print(md)
    elif a.golden:
        cases = [json.loads(l) for l in Path(a.golden).read_text(encoding="utf-8").splitlines() if l.strip()]
        results, t0 = [], time.time()
        for c in cases:
            kept, dropped = review_doc(c["text"], c.get("doc_type", "public"), rules, llm, cheap, strong)
            results.append((c, kept))
            (out / f"{c['id']}.md").write_text(to_markdown(c["id"], c["text"], kept, dropped, rules), encoding="utf-8")
        if a.eval:
            m = evaluate(results)
            m["mode"] = "offline-keyword" if a.offline else f"llm({cheap}->{strong})"
            m["cases"] = len(cases)
            m["seconds"] = round(time.time() - t0, 1)
            if llm:
                m["usage"] = llm.usage
                m["cost_per_doc_usd"] = round(llm.usage["cost_usd"] / len(cases), 5)
                m["tokens_per_doc"] = round((llm.usage["prompt_tokens"] + llm.usage["completion_tokens"]) / len(cases))
            (out / "eval.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
