# Kachu v2 — 部署 Runbook

> 適用版本：v2 beta  
> 更新日期：2026-04-28  
> 目標環境：單台 Linux 主機（Docker + nginx + Let's Encrypt）  
> 估計總時間：首次約 60–90 分鐘，之後更新約 10 分鐘

---

## 前置需求

| 項目 | 說明 |
|---|---|
| Docker Engine ≥ 24 | `docker --version` 確認 |
| Docker Compose v2 | `docker compose version` 確認（注意是空格，不是 `-`） |
| 公有 IP + 網域 | 需要能讓 LINE 與 Google 打 webhook，建議 `app.kachu.tw` |
| DNS A record 已指向主機 IP | Let's Encrypt 需要 80 port 回應 |
| `.env` 中所有 `REPLACE_WITH_*` 已填妥 | 見步驟一 |

---

## 步驟一：填妥生產環境變數

### 1-1 複製並編輯 `.env.prod`

所有 `REPLACE_WITH_*` 的欄位，照下表填入：

| 欄位 | 來源 | 說明 |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | [LINE Developers Console](https://developers.line.biz/) → Channel → Messaging API | 長效 token |
| `LINE_CHANNEL_SECRET` | 同上 → Basic settings | Channel Secret |
| `LINE_BOSS_USER_ID` | LINE 開發者工具，或傳訊息給 bot 後從 webhook payload 取得 `source.userId` |
| `POSTGRES_PASSWORD` | 自訂，至少 20 個字元隨機字串 | 同時更新 `DATABASE_URL` 裡的密碼 |
| `DATABASE_URL` | `postgresql+psycopg://kachu:<POSTGRES_PASSWORD>@postgres:5432/kachu` | 替換密碼 |
| `GOOGLE_AI_API_KEY` | [Google AI Studio](https://aistudio.google.com/) → API Keys | Gemini 用 |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/) → API Keys | Embeddings 用 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | [GCP Console](https://console.cloud.google.com/) → API & Services → Credentials | OAuth 2.0 Client |
| `META_APP_SECRET` | [Meta for Developers](https://developers.facebook.com/) → App → Settings | 若不用 Meta 功能可留空並確認 `FEATURE_META=False` |
| `SECRET_KEY` | 執行 `python -c "import secrets; print(secrets.token_hex(32))"` 產生 | 至少 32 字元 |
| `TOKEN_ENCRYPTION_KEY` | 執行 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 產生 | Fernet key |

> ⚠️ `DATABASE_URL` 裡的 `<POSTGRES_PASSWORD>` 和 `POSTGRES_PASSWORD` 欄位必須完全一致。

### 1-2 確認 Google service account 檔案

```bash
ls credentials/google-service-account.json
```

此檔案應存在且屬於 `contentflow-gsc@opsly-492412.iam.gserviceaccount.com`。  
確認 GCP Console 上這個 service account 仍有效，且對應的 API 已啟用：
- Google Business Profile API
- Google Search Console API（若有用到）

---

## 步驟二：首次取得 TLS 憑證（Let's Encrypt）

> 若用 IP 直接測試（不走 HTTPS）可跳過此步，改用 `infra/nginx/nginx.init.conf`。

### 2-1 先用初始化 nginx 設定啟動（只跑 port 80）

```bash
# 暫時讓 nginx 只聽 80，以便 ACME challenge 可以通過
docker compose -f docker-compose.prod.yml run --rm --entrypoint "" \
  gateway nginx -c /etc/nginx/nginx.conf
```

若 `infra/nginx/nginx.init.conf` 存在，先掛載它：

```bash
docker run --rm -d -p 80:80 \
  -v $(pwd)/infra/nginx/nginx.init.conf:/etc/nginx/nginx.conf:ro \
  -v certbot_www:/var/www/certbot \
  --name nginx-init nginx:alpine
```

### 2-2 申請憑證

```bash
docker run --rm \
  -v certbot_certs:/etc/letsencrypt \
  -v certbot_www:/var/www/certbot \
  certbot/certbot certonly --webroot \
  --webroot-path=/var/www/certbot \
  --email your-email@example.com \
  --agree-tos --no-eff-email \
  -d app.kachu.tw

# 停掉臨時 nginx
docker stop nginx-init
```

---

## 步驟三：啟動所有服務

### 3-1 確認 AgentOS 目錄存在

`docker-compose.prod.yml` 會 build `../agentOS-v2`。確認路徑正確：

```bash
ls ../agentOS-v2/Dockerfile
```

> 若 agentOS 目錄名稱不同（例如 `../AgentOS`），編輯 `docker-compose.prod.yml` 的 `agentos.build.context` 欄位。

### 3-2 Build 並啟動

```bash
# 在 Kachu-v2 目錄下執行
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

### 3-3 確認所有容器健康

```bash
docker compose -f docker-compose.prod.yml ps
```

預期所有服務狀態為 `healthy` 或 `running`：

```
NAME        STATUS              PORTS
postgres    healthy
agentos     healthy
kachu       healthy
gateway     running             0.0.0.0:80->80, 0.0.0.0:443->443
certbot     running
```

如果某個服務反覆 restart，查看 log：

```bash
docker compose -f docker-compose.prod.yml logs kachu --tail=50
docker compose -f docker-compose.prod.yml logs agentos --tail=50
```

---

## 步驟四：執行資料庫 Migration

> 只有第一次部署或有 schema 變動時需要執行。

```bash
# 進入 kachu 容器執行 migration
docker compose -f docker-compose.prod.yml exec kachu \
  alembic upgrade head
```

預期輸出：

```
INFO  [alembic.runtime.migration] Running upgrade  -> 20260427_0001, baseline schema
```

若看到 `Target database is not up to date` 或 migration 失敗，查看：

```bash
docker compose -f docker-compose.prod.yml exec kachu alembic history
docker compose -f docker-compose.prod.yml exec kachu alembic current
```

---

## 步驟五：設定 LINE Webhook URL

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 進入 Kachu 對應的 Channel → **Messaging API** 頁籤
3. 將 Webhook URL 設為：`https://app.kachu.tw/webhooks/line`
4. 點擊 **Verify** — 應看到 `200 OK`
5. 確認 **Use webhook** 已啟用

---

## 步驟六：健康確認

```bash
# 從外部打 health endpoint
curl -sf https://app.kachu.tw/health && echo "OK"
```

回應應為：`{"status":"ok"}` 或類似結構。

---

## 日常更新流程（之後每次 deploy）

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build kachu
# 若有 schema 變動：
docker compose -f docker-compose.prod.yml exec kachu alembic upgrade head
```

---

## 常見問題

| 症狀 | 可能原因 | 處理方式 |
|---|---|---|
| `kachu` 容器啟動後立即退出 | `.env.prod` 有 `REPLACE_WITH_*` 未填，production guard 攔截 | 查看 `docker logs kachu`，確認是哪個設定缺漏 |
| `agentos` 容器 `unhealthy` | build context 路徑不對，或 AgentOS 本身有問題 | 確認 `../agentOS-v2/Dockerfile` 存在 |
| LINE Webhook Verify 失敗 | nginx 尚未跑起來，或 DNS 未生效 | `curl http://app.kachu.tw/health` 確認 80 port 是否通 |
| Alembic 報 `Can't locate revision` | baseline 版本不在 DB 版本表 | `alembic stamp head` 後重試（僅限全新 DB） |
| `TOKEN_ENCRYPTION_KEY` 錯誤 | 不是有效的 Fernet key | 重新用 `Fernet.generate_key()` 產生 |
