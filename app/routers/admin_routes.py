"""模型管理与调用记录路由。"""
import json

from fastapi import APIRouter, HTTPException, Request

from .. import deps, model_calls
from ..db import db

router = APIRouter()


@router.get("/api/chat-models")
def list_chat_models(request: Request):
    deps.me(request)
    return deps.available_chat_models()


@router.get("/api/admin/chat-models")
def get_admin_chat_models(request: Request):
    if deps.me(request)["role"] != "admin":
        raise HTTPException(403, "仅管理员可管理模型")
    return {"models": deps.gateway_chat_models(), **deps.chat_model_settings()}


@router.put("/api/admin/chat-models")
async def update_admin_chat_models(request: Request):
    if deps.me(request)["role"] != "admin":
        raise HTTPException(403, "仅管理员可管理模型")
    body = await request.json()
    allowed, default = body.get("allowed"), body.get("default")
    if (not isinstance(allowed, list) or not allowed or len(allowed) > 500
            or any(not isinstance(m, str) or not m or len(m) > 200 for m in allowed)
            or len(set(allowed)) != len(allowed) or not isinstance(default, str) or default not in allowed):
        raise HTTPException(400, "请选择至少一个模型，并从中指定默认模型")
    available = {m["id"] for m in deps.gateway_chat_models()}
    if any(model not in available for model in allowed):
        raise HTTPException(400, "所选模型已不在网关列表中，请刷新后重新选择")
    with db() as con:
        con.execute(
            "INSERT INTO chat_model_settings(id,allowed_json,default_model) VALUES (1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET allowed_json=excluded.allowed_json, default_model=excluded.default_model",
            (json.dumps(allowed, ensure_ascii=False), default),
        )
    return {"allowed": allowed, "default": default}


@router.get("/api/model-calls")
def list_model_calls(request: Request, conversation_id: int | None = None, document_id: int | None = None):
    if conversation_id is not None and document_id is not None:
        raise HTTPException(400, "请选择一种记录范围")
    return model_calls.visible_to(deps.me(request)["id"], conversation_id=conversation_id, document_id=document_id)
