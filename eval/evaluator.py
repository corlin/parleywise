"""黄金集评测指标计算。"""
from apps.compliance.report import group_snippets


def evaluate(results, rules):
    """strict：只认 expected；lenient：命中 acceptable（可接受的次要命中）不算误报。
    片段级：按合并后的片段计，片段内任一规则在 expected 或 acceptable 中即算对。"""
    tp = fp = fp_strict = fn = 0
    snip_total = snip_ok = rule_hits = 0
    per_rule_miss, per_rule_fp, snip_fp = {}, {}, {}
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
        rule_hits += len(kept)
        for g in group_snippets(kept, case["text"], rules):
            ids = [f["rule_id"] for f in g]
            snip_total += 1
            if set(ids) & (exp | ok):
                snip_ok += 1
            else:
                snip_fp.setdefault(case["id"], []).append(ids)
    r3 = lambda a, b: round(a / b, 3) if b else None
    n = len(results)
    return {"tp": tp, "fn": fn, "recall": r3(tp, tp + fn),
            "fp_strict": fp_strict, "precision_strict": r3(tp, tp + fp_strict),
            "fp_lenient": fp, "precision_lenient": r3(tp, tp + fp),
            "missed": per_rule_miss, "false_positives_lenient": per_rule_fp,
            "snippet_precision": r3(snip_ok, snip_total),
            "snippet_count_per_doc": round(snip_total / n, 2) if n else None,
            "rule_hits_per_doc": round(rule_hits / n, 2) if n else None,
            "snippet_false_positives": snip_fp}
