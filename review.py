#!/usr/bin/env python3
"""Parleywise 审查流水线 CLI 入口与兼容外观。

核心实现已拆分为：
- runtime/：环境变量与 LiteLLM 网关调用
- apps/compliance/：规则加载、初审/复核/校验流水线与报告格式化
- eval/：黄金集评测指标计算
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from apps.compliance import (  # noqa: F401
    INDEPENDENT_TRIAGE_SYS,
    NEGATION,
    REVIEW_SYS,
    SEVERITY_RANK,
    TRIAGE_SYS,
    applicable,
    group_snippets,
    keyword_scan,
    llm_review,
    load_rules,
    review_doc,
    rules_block,
    snippet_quotes,
    to_markdown,
    to_record,
    verify,
)
from eval import evaluate  # noqa: F401
from runtime import LLM, load_dotenv, parse_findings, today  # noqa: F401

HERE = Path(__file__).resolve().parent


def _code_sha256():
    h = hashlib.sha256()
    for rel in ("review.py", "runtime/llm.py", "apps/compliance/pipeline.py", "eval/evaluator.py"):
        h.update((HERE / rel).read_bytes())
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file")
    ap.add_argument("--doc-type", default="public", choices=["public", "private"])
    ap.add_argument("--golden")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--collaboration", action="store_true", help="实验：两个独立初审角色并行，复核汇总")
    ap.add_argument("--trace-collaboration", action="store_true", help="在结果 JSON 中保存各角色原始意见（可能含材料内容）")
    ap.add_argument("--out", default=str(HERE / "out"))
    a = ap.parse_args()
    if a.offline and a.collaboration:
        ap.error("--collaboration 需要模型调用，不能与 --offline 同用")
    if a.trace_collaboration and not a.collaboration:
        ap.error("--trace-collaboration 需要 --collaboration")
    load_dotenv()
    # 同一次评测即使跨午夜也使用同一天，便于重现。
    os.environ.setdefault("PARLEYWISE_TODAY", datetime.date.today().isoformat())

    rules = load_rules()
    llm = None if a.offline else LLM()
    if llm is not None and not llm.key:
        sys.exit("未设置 LITELLM_API_KEY；如只想跑关键词基线，请加 --offline")
    cheap = os.environ.get("PARLEYWISE_MODEL_CHEAP", "cheap")
    strong = os.environ.get("PARLEYWISE_MODEL_STRONG", "strong")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.text_file:
        text = Path(a.text_file).read_text(encoding="utf-8").strip()
        trace = []
        kept, dropped = review_doc(text, a.doc_type, rules, llm, cheap, strong, collaboration=a.collaboration,
                                   on_collaboration=trace.append if a.trace_collaboration else None)
        md = to_markdown(Path(a.text_file).stem, text, kept, dropped, rules)
        (out / (Path(a.text_file).stem + ".md")).write_text(md, encoding="utf-8")
        rec = to_record(Path(a.text_file).stem, text, a.doc_type, kept, dropped, rules)
        if a.trace_collaboration:
            rec["collaboration_trace"] = trace
        (out / (Path(a.text_file).stem + ".json")).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(md)
    elif a.golden:
        cases = [json.loads(l) for l in Path(a.golden).read_text(encoding="utf-8").splitlines() if l.strip()]
        if not cases:
            sys.exit("黄金集为空，无法评测")
        ids = [c.get("id") for c in cases]
        if any(not isinstance(i, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids) or len(ids) != len(set(ids)):
            sys.exit("黄金集样例 ID 须唯一且只含字母、数字、下划线或短横线")
        sha256 = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        manifest = {
            "dataset_sha256": sha256(a.golden),
            "rules_sha256": sha256(HERE / "rules.json"),
            "code_sha256": _code_sha256(),
            "effective_date": today(),
            "cheap_model": None if a.offline else cheap,
            "strong_model": None if a.offline else strong,
            "offline": a.offline,
        }
        results, failures, t0 = [], [], time.time()
        for c in cases:
            trace = []
            try:
                kept, dropped = review_doc(c["text"], c.get("doc_type", "public"), rules, llm, cheap, strong,
                                           collaboration=a.collaboration,
                                           on_collaboration=trace.append if a.trace_collaboration else None)
            except RuntimeError as e:  # 保留失败记录，不把请求失败当作模型漏报
                print(f"{c['id']} 失败：{e}", file=sys.stderr)
                failures.append(c["id"])
                kept, dropped = [], []
            if c["id"] not in failures:
                results.append((c, kept))
            report = (f"# 合规初审报告：{c['id']}\n\n> 处理失败，未完成审查；不得视为未发现问题。\n"
                      if c["id"] in failures else to_markdown(c["id"], c["text"], kept, dropped, rules))
            (out / f"{c['id']}.md").write_text(report, encoding="utf-8")
            rec = to_record(c["id"], c["text"], c.get("doc_type", "public"), kept, dropped, rules)
            rec["status"] = "failed" if c["id"] in failures else "completed"
            if a.trace_collaboration:
                rec["collaboration_trace"] = trace
            (out / f"{c['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        if a.eval:
            m = evaluate(results, rules)
            m["mode"] = "offline-keyword" if a.offline else f"{'collaboration' if a.collaboration else 'llm'}({cheap}->{strong})"
            m["cases"] = len(cases)
            m["completed_cases"] = len(results)
            m["failed_cases"] = failures
            m["manifest"] = manifest
            m["seconds"] = round(time.time() - t0, 1)
            if llm:
                m["usage"] = llm.usage
                m["cost_per_doc_usd"] = (round(llm.usage["cost_usd"] / len(cases), 5)
                                         if cases and not llm.usage["calls_without_cost"] else None)
                m["tokens_per_doc"] = round((llm.usage["prompt_tokens"] + llm.usage["completion_tokens"]) / len(cases))
            (out / "eval.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(m, ensure_ascii=False, indent=2))
        if failures:
            sys.exit(1)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
