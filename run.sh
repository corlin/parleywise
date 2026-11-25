#!/bin/sh
# 启动 Parleywise。必须单进程（审查任务在进程内线程池里跑），不要加 --workers。
# 局域网试点：PARLEYWISE_HOST=0.0.0.0 ./run.sh ；前面最好有 HTTPS 反向代理，并设 PARLEYWISE_SECURE_COOKIE=1
cd "$(dirname "$0")"
exec uv run python -m uvicorn app.main:app --host "${PARLEYWISE_HOST:-127.0.0.1}" --port "${PARLEYWISE_PORT:-8000}" --proxy-headers
