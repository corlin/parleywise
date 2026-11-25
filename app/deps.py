"""后端共享配置、依赖与模型网关辅助函数。"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402

from .db import db  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
MAX_CHARS = 30000
MAX_UPLOAD = 10 * 1024 * 1024
REJECT_REASONS = {
    "not_violation": "不构成违规",
    "not_applicable": "规则不适用",
    "bad_quote": "片段定位不准",
    "duplicate": "与其他意见重复",
    "other": "其他",
}
CONCLUSIONS = {"pass": "通过", "revise": "修改后通过", "reject": "不通过"}
SECURE_COOKIE = os.environ.get("PARLEYWISE_SECURE_COOKIE") == "1"  # 走 HTTPS 时设为 1

review.load_dotenv()
RULES = review.load_rules()
SOURCES = json.loads((ROOT / "rules.json").read_text(encoding="utf-8")).get("sources", {})
CHEAP = os.environ.get("PARLEYWISE_MODEL_CHEAP") or "cheap"
STRONG = os.environ.get("PARLEYWISE_MODEL_STRONG") or "strong"


def me(request):
    return request.state.user


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _s(v):
    return str(v or "").strip()


def gateway_chat_models():
    """使用聊天所用密钥读取网关允许访问的模型别名。"""
    llm = review.LLM()
    if not llm.key:
        raise HTTPException(503, "未配置模型网关密钥")
    req = urllib.request.Request(llm.base + "/v1/models", headers={"Authorization": f"Bearer {llm.key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read(1024 * 1024 + 1))
        models = payload.get("data")
        if not isinstance(models, list):
            raise ValueError()
        result = []
        for model in models:
            ident = model.get("id") if isinstance(model, dict) else None
            if (isinstance(ident, str) and ident.strip() and ident == ident.strip() and len(ident) <= 200
                    and not any(item["id"] == ident for item in result)):
                result.append({"id": ident, "name": ident})
        return result
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(502, "无法获取网关模型列表，请检查网关连接和密钥权限") from exc


def chat_model_settings():
    with db() as con:
        row = con.execute("SELECT allowed_json, default_model FROM chat_model_settings WHERE id=1").fetchone()
    if row:
        return {"allowed": json.loads(row["allowed_json"]), "default": row["default_model"]}
    # 既有部署先沿用初筛和复核模型，避免升级后默认暴露网关全部模型。
    allowed = list(dict.fromkeys([CHEAP, STRONG]))
    default = os.environ.get("PARLEYWISE_DEFAULT_CHAT_MODEL") or CHEAP
    return {"allowed": allowed, "default": default if default in allowed else allowed[0]}


def available_chat_models():
    settings = chat_model_settings()
    models = [m for m in gateway_chat_models() if m["id"] in settings["allowed"]]
    return {"models": models, "default": settings["default"] if any(m["id"] == settings["default"] for m in models) else None}
