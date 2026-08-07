#!/bin/sh
# Production 啟動流程：先把 DB schema 升到最新（冪等），成功才開始服務。
# migration 失敗 → set -e 讓容器直接啟動失敗（fail fast），不帶壞 schema 上線。
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting gunicorn (workers=${WEB_CONCURRENCY:-2})..."
# exec：gunicorn 取代 shell 成為 PID 1，才能正確收到 docker stop 的 SIGTERM。
# --timeout 300：application kit 生成最長可達 90s+，預設 30s 會誤殺 worker。
# --forwarded-allow-ips="*"：信任 X-Forwarded-For（來源是 nginx 容器 IP，非預設
# 信任的 127.0.0.1）。不設的話所有使用者的 request.client.host 都是 nginx IP，
# per-IP rate limit 變成全站共用一個計數桶。backend port 未對外開放（僅 compose
# 內部網路），"*" 不會被外部偽造 header 利用。
exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "${WEB_CONCURRENCY:-2}" \
  --bind 0.0.0.0:8000 \
  --timeout 300 \
  --forwarded-allow-ips "*" \
  --access-logfile - \
  --error-logfile -
