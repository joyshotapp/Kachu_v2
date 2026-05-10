#!/bin/bash
# 在 kachu-plus 容器內執行 alembic migration（部署腳本或手動操作用）
set -e
cd /opt/kachu-plus
docker run --rm \
  --network kachu-plus_default \
  -v /opt/kachu-plus/src:/app/src \
  -v /opt/kachu-plus/alembic:/app/alembic \
  -v /opt/kachu-plus/alembic.ini:/app/alembic.ini \
  -v /opt/kachu-plus/pyproject.toml:/app/pyproject.toml \
  --env-file /opt/kachu-plus/.env.prod \
  -e DATABASE_URL="postgresql+psycopg://kachu_plus:${POSTGRES_PASSWORD}@postgres:5432/kachu_plus" \
  kachu-plus-kachu-plus \
  alembic upgrade head
