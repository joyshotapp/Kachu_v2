# Kachu+

Kachu+ 是 SMB（個人商家）的 AI 商業夥伴 SaaS。透過 LINE 作為主要指揮介面，整合 Google Business Profile、Meta（Facebook / Instagram）留言與評論，幫商家自動草擬回覆、安排發文、追蹤沉睡顧客。

**v1 狀態：已通過 161 個測試，核心主路徑（onboarding、approval、webhook、顧客治理、admin event hub）均已完成，尚未進入正式生產部署。**

---

## 工作區結構

```
src/kachu_plus/     — 主要後端（FastAPI + SQLModel）
tests/              — 單元與整合測試（pytest）
alembic/versions/   — 資料庫 migration
docs/               — 設計決策與審核報告
Dockerfile          — 單容器映像
docker-compose.prod.yml — 生產部署（Postgres + Kachu+）
.env.prod.example   — 環境變數範本
```

---

## Python 版本要求

**Python 3.11 以上**，這是硬性要求。本地通常使用 pyenv 管理：

```bash
pyenv install 3.11.9
pyenv local 3.11.9
```

> **重要**：AgentOS_real（相鄰 repo）有自己的 venv，不可與 Kachu+ 共用。兩者分開管理。

---

## 本機安裝

```bash
cd /Users/yuchuchen/Desktop/Kachu+

python3.11 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

---

## 環境變數

```bash
cp .env.prod.example .env
```

本機開發只需要設定以下最小集合：

```
DATABASE_URL=sqlite:////path/to/kachu.db
FIELD_ENCRYPTION_KEY=<fernet key>
ADMIN_API_TOKEN=<任意 token>
LITELLM_MODEL=gemini/gemini-2.0-flash
GOOGLE_AI_API_KEY=<your key>
```

產生 Fernet key：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> **LINE / Google / Meta 憑證不放 `.env`**：`line_channel_secret`、`line_channel_access_token`、Google / Meta access token 等 tenant 級憑證，是透過 Admin API 寫入資料庫的 connector 記錄，不是靠環境變數注入。啟動後需先呼叫 `POST /admin/tenants` 建立商家，再設定各渠道 connector。詳見 `docs/SOP_商家導入與開通流程_工程版.md`。

---

## 執行測試

```bash
source .venv/bin/activate
pytest -q
```

預期輸出：`161 passed`（或以上）。

---

## 本機啟動

```bash
source .venv/bin/activate

mkdir -p .tmp
DATABASE_URL=sqlite:////Users/yuchuchen/Desktop/Kachu+/.tmp/kachu.db \
PYTHONPATH=src \
uvicorn kachu_plus.main:create_app --factory --host 127.0.0.1 --port 8001
```

啟動後可訪問：`http://127.0.0.1:8001/docs`

---

## 資料庫 Migration

```bash
source .venv/bin/activate

# 套用所有 migration
alembic upgrade head

# 新增 migration（請以日期開頭命名）
alembic revision --autogenerate -m "20260510_0016_describe_change"
```

> **本機 SQLite 開發例外**：測試與本機啟動（`DATABASE_URL=sqlite://...`）透過 `SQLModel.metadata.create_all()` 在 startup 自動建表，不需要手動執行 `alembic upgrade head`。Migration 主要用於正式環境 Postgres 的結構版本控制。

---

## Admin API

所有 `/admin/*` 路由需要 Bearer token：

```
Authorization: Bearer <ADMIN_API_TOKEN>
```

主要操作面：
- `POST /admin/tenants` — 建立商家
- `GET /admin/tenants/{tenant_id}/events` — 查詢 event hub
- `POST /admin/tenants/{tenant_id}/events/{event_id}/replay` — 重放單一事件
- `POST /admin/tenants/{tenant_id}/events/replay-query` — 條件查詢後批次重放（支援 `missing_engagement` / `missing_pending_approval` policy）

---

## 與 AgentOS_real 的關係

AgentOS_real 是 Kachu+ 的 execution runtime，負責 workflow task / run / approval lifecycle。
兩者透過 HTTP 對接，需要分別啟動：

```bash
# AgentOS_real（需在自己的工作目錄，使用 Python 3.11+）
cd /Users/yuchuchen/Desktop/AgentOS_real
source .venv/bin/activate   # 必須是 AgentOS_real 的 venv
mkdir -p .tmp
KACHU_BASE_URL=http://127.0.0.1:8001 \
DATABASE_URL=sqlite:////Users/yuchuchen/Desktop/AgentOS_real/.tmp/agentos.db \
PYTHONPATH=src \
uvicorn agent_platform.main:app --host 127.0.0.1 --port 8000
```

> AgentOS_real 要求 Python 3.11+；若使用 3.10 會因 `StrEnum` 匯入失敗而無法啟動。

---

## 主要文件入口

| 文件 | 用途 |
|---|---|
| `Kachu+_產品定義文件.md` | 完整產品定義與開發任務清單（§0.5 為 RD 必讀） |
| `docs/2026-05-10_四來源專案對照審核矩陣.md` | 實作進度追蹤矩陣 |
| `docs/2026-05-10_Super8_與_Cresclab_借鏡整合完整評估報告.md` | 架構借鏡評估報告 |
| `docs/deploy-runbook.md` | 生產部署操作手冊 |
| `docs/SOP_商家導入與開通流程_工程版.md` | 第一個商家開通的完整步驟（Admin API payload + connector 設定） |
