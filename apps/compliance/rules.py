"""合规规则库加载与过滤。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def load_rules(path=ROOT / "rules.json"):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {r["id"]: r for r in data["rules"]}


def applicable(rules, doc_type):
    return {k: r for k, r in rules.items()
            if r["check"] != "manual" and r.get("applies_to", doc_type) == doc_type}


def rules_block(rules):
    return "\n".join(f"{r['id']}｜{r['title']}：{r['description']}" for r in rules.values())
