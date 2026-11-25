"""Parleywise 后端应用入口。

启动（必须单进程：审查任务跑在进程内的线程池里）：
  uv run uvicorn app.main:app --port 8000
"""
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .db import init
from .deps import (  # noqa: F401
    APP_DIR,
    available_chat_models,
    chat_model_settings,
    gateway_chat_models,
    me,
)
from .routers import admin_routes, auth_routes, chat_routes, review_routes
from .routers.admin_routes import update_admin_chat_models  # noqa: F401
from .routers.chat_routes import send_conversation_message  # noqa: F401
from .routers.review_routes import metrics, requeue_interrupted  # noqa: F401

init()
requeue_interrupted()

app = FastAPI(title="Parleywise", docs_url=None, redoc_url=None, openapi_url=None)
PUBLIC_API = {"/api/login", "/api/health", "/api/registration", "/api/register"}


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API:
        user = auth.user_for(request.cookies.get(auth.SESSION_COOKIE))
        if not user:
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        request.state.user = user
    resp = await call_next(request)
    if path == "/" or path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"  # 改了前端刷新即生效
    return resp


app.include_router(auth_routes.router)
app.include_router(admin_routes.router)
app.include_router(chat_routes.router)
app.include_router(review_routes.router)

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(APP_DIR / "static" / "index.html")
