FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

COPY . .

EXPOSE 8001

# --factory: uvicorn calls create_app() at startup; no module-level side effects in tests
CMD ["uvicorn", "kachu.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
