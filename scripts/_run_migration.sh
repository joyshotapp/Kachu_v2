#!/bin/bash
set -e
cd /opt/kachu-v2
docker run --rm \
  --network kachu-v2_default \
  -v /opt/kachu-v2/src:/app/src \
  -v /opt/kachu-v2/alembic:/app/alembic \
  -v /opt/kachu-v2/alembic.ini:/app/alembic.ini \
  -v /opt/kachu-v2/pyproject.toml:/app/pyproject.toml \
  --env-file /opt/kachu-v2/.env.prod \
  kachu-v2-kachu \
  alembic upgrade head
