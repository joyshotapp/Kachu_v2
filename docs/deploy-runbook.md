# Kachu+ Production Deploy Runbook

版本：1.0  
更新：2026-05-09  
適用環境：Linode 172.234.85.159（與 Kachu v2 共用同一台伺服器）

---

## 架構說明

```
172.234.85.159
├── /opt/kachu-v2/          ← v2（繼續運行，不影響）
│     └── kachu-v2-gateway-1（nginx，佔用 80/443）
│
├── /opt/AgentOS_real/      ← Kachu+ 專屬 AgentOS（由 deploy.py 同步）
│
└── /opt/kachu-plus/        ← Kachu+（本文件的目標）
      ├── kachu-plus-postgres-1   內部 5432
      ├── kachu-plus-agentos-1    內部 8000
      └── kachu-plus-kachu-plus-1 host:127.0.0.1:8002
                                   ↑
                       v2 nginx proxy_pass 過來
                       (plus.kachu.tw → 8002)
```

---

## 首次部署 SOP

### 步驟 0：前置確認清單

- [ ] DNS：`plus.kachu.tw` A record → `172.234.85.159`（先確認，certbot 要用）
- [ ] 本機有 SSH 連線：`ssh root@172.234.85.159 "echo ok"`
- [ ] `AgentOS_real/` 在本機路徑 `/Users/yuchuchen/Desktop/AgentOS_real`（或調整 `--local-agentos`）
- [ ] `.env.prod` 已從 `.env.prod.example` 填妥（見步驟 1）

### 步驟 1：建立 .env.prod

```bash
cd /Users/yuchuchen/Desktop/Kachu+
cp .env.prod.example .env.prod
```

編輯 `.env.prod`，填入以下值：

| Key | 說明 | 來源 |
|-----|------|------|
| `POSTGRES_PASSWORD` | 自訂強密碼（≥ 24 字元） | 自產：`openssl rand -hex 16` |
| `FIELD_ENCRYPTION_KEY` | Fernet key | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `ADMIN_API_TOKEN` | Admin API Bearer token | `openssl rand -hex 32` |
| `GOOGLE_AI_API_KEY` | Gemini API key | 從 v2 `.env.prod` 複製 |
| `OPENAI_API_KEY` | OpenAI key（選填） | 從 v2 `.env.prod` 複製 |
| `GOOGLE_OAUTH_CLIENT_ID` | GBP OAuth client | 從 v2 `.env.prod` 複製 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | GBP OAuth secret | 從 v2 `.env.prod` 複製 |
| `META_APP_ID` | Meta App ID | Meta App dashboard |
| `META_APP_SECRET` | Meta App secret | Meta App dashboard |
| `META_WEBHOOK_VERIFY_TOKEN` | Meta webhook verify token | 自產：`openssl rand -hex 24` |

> ⚠️ `.env.prod` 已在 `.gitignore`，絕對不要 commit。

### API Key / 憑證盤點

正式導入前，至少要先備齊以下資料。

| 類型 | Key / 憑證 | 必填 | 用途 |
|-----|-----------|------|------|
| 系統層 | `POSTGRES_PASSWORD` | 是 | production DB |
| 系統層 | `FIELD_ENCRYPTION_KEY` | 是 | 加密 LINE / connector 憑證 |
| 系統層 | `ADMIN_API_TOKEN` | 是 | 建 tenant / patch 憑證 |
| 系統層 | `GOOGLE_AI_API_KEY` | 建議必填 | photo analyze、FAQ、顧問回覆主力 LLM |
| 系統層 | `OPENAI_API_KEY` | 選填 | LLM 備援 provider |
| 系統層 | `GOOGLE_OAUTH_CLIENT_ID` | Google 串接時必填 | Google Business OAuth |
| 系統層 | `GOOGLE_OAUTH_CLIENT_SECRET` | Google 串接時必填 | Google Business OAuth |
| 系統層 | `META_APP_ID` | Meta 串接時必填 | Meta OAuth 與 Graph API |
| 系統層 | `META_APP_SECRET` | Meta 串接時必填 | Meta OAuth token exchange |
| 系統層 | `META_WEBHOOK_VERIFY_TOKEN` | Meta 留言/私訊 webhook 時必填 | 驗證 `/meta/webhook` |
| Tenant 層 | `LINE_CHANNEL_ID` | 是 | tenant webhook 綁定 |
| Tenant 層 | `LINE_CHANNEL_SECRET` | 是 | webhook / postback 驗簽 |
| Tenant 層 | `LINE_CHANNEL_ACCESS_TOKEN` | 是 | push onboarding / approval / FAQ 回覆 |
| Tenant 層 | `BOSS_LINE_USER_ID` | 是 | 初始 owner 綁定 |
| Tenant 層 | Google `account_id` | Google 發布時必填 | Google Business target account |
| Tenant 層 | Google `location_id` | Google 發布時必填 | Google Business target location |
| Tenant 層 | Google `access_token` | Google 發布時必填 | review / local post API |
| Tenant 層 | Google `refresh_token` | 強烈建議 | access token 過期後刷新 |
| Tenant 層 | Meta `access_token` | Meta 發布/insights 時必填 | Instagram Graph API |
| Tenant 層 | Meta `fb_page_id` | Meta 發布/insights 時必填 | Facebook Page target |
| Tenant 層 | Meta `ig_user_id` | IG 發布/insights 時建議必填 | Instagram Business target |
| Tenant 層 | Meta `fb_access_token` | 選填 | 若 Facebook 與 IG token 分離時使用 |

> 補充：photo content 若要真的帶圖發布到 Meta，還需要「可公開抓取的圖片 URL」。目前只有 LINE 原始圖時，系統會先退回文字發布，不額外需要新的 API key，但後續若要正式上線建議補一層物件儲存。

### 步驟 2：執行一鍵部署腳本

```bash
cd /Users/yuchuchen/Desktop/Kachu+
python scripts/deploy.py --host root@172.234.85.159
```

腳本會依序執行 8 個步驟：

| # | 步驟 | 說明 |
|---|------|------|
| 1 | rsync Kachu+ | 同步程式碼到 /opt/kachu-plus |
| 2 | rsync AgentOS | 同步到 /opt/AgentOS_real |
| 3 | docker build | 建構兩個映像檔 |
| 4 | docker up | 啟動所有容器 |
| 5 | alembic migrate | 跑 9 個 migration（建立所有 tables） |
| 6 | 申請 SSL 憑證 | certbot for plus.kachu.tw |
| 7 | 更新 nginx | 在 v2 nginx 加入 plus.kachu.tw server block |
| 8 | Smoke test | health check + HTTPS curl 驗證 |

### 步驟 3：設定第一個商家 tenant

```bash
# 3-1. 產生 FIELD_ENCRYPTION_KEY 加密後的 channel credentials
#      （若 FIELD_ENCRYPTION_KEY 為空，直接填明文）

# 3-2. 呼叫 Admin API 建立 tenant
curl -X POST https://plus.kachu.tw/admin/tenants \
  -H "Authorization: Bearer <ADMIN_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "U1f7215a15f956a462bd196b19cc30f87",
    "display_name": "我的商店",
    "line_channel_id": "<LINE_CHANNEL_ID>",
    "line_channel_secret": "<LINE_CHANNEL_SECRET>",
    "line_channel_access_token": "<LINE_CHANNEL_ACCESS_TOKEN>",
    "boss_line_user_id": "<BOSS_LINE_USER_ID>",
    "industry": "food",
    "sleep_threshold_days": 60
  }'
```

### 步驟 4：更新 LINE Webhook URL

登入 [LINE Developers Console](https://developers.line.biz/) → Messaging API → Webhook URL：

```
https://plus.kachu.tw/webhooks/line/<tenant_id>
```

---

## 日常維運

### 查看 log

```bash
# Kachu+ 應用 log
ssh root@172.234.85.159 'docker logs --tail=100 -f kachu-plus-kachu-plus-1'

# AgentOS log
ssh root@172.234.85.159 'docker logs --tail=50 kachu-plus-agentos-1'

# 所有容器狀態
ssh root@172.234.85.159 'docker compose -f /opt/kachu-plus/docker-compose.prod.yml ps'
```

### 只更新 Kachu+（快速部署）

```bash
python scripts/deploy.py --host root@172.234.85.159 \
    --services kachu-plus \
    --skip-agentos-sync --skip-ssl --skip-nginx
```

### 查詢 DB

```bash
# 對話紀錄
ssh root@172.234.85.159 "docker exec kachu-plus-postgres-1 psql -U kachu_plus -d kachu_plus \
  -c \"SELECT tenant_id, actor_role, LEFT(content_text,80), created_at FROM kachu_conversations ORDER BY created_at DESC LIMIT 20;\""

# 顧客 profile
ssh root@172.234.85.159 "docker exec kachu-plus-postgres-1 psql -U kachu_plus -d kachu_plus \
  -c 'SELECT * FROM kachu_customer_profiles LIMIT 10;'"

# 主動建議
ssh root@172.234.85.159 "docker exec kachu-plus-postgres-1 psql -U kachu_plus -d kachu_plus \
  -c 'SELECT * FROM kachu_suggestions ORDER BY created_at DESC LIMIT 10;'"
```

### 手動執行 migration

```bash
ssh root@172.234.85.159 'bash /opt/kachu-plus/infra/run_migration.sh'
```

### 啟用 Meta OAuth

1. 編輯 `/opt/kachu-plus/.env.prod`，至少確認以下值已填入：

```bash
META_APP_ID=<META_APP_ID>
META_APP_SECRET=<META_APP_SECRET>
META_WEBHOOK_VERIFY_TOKEN=<META_WEBHOOK_VERIFY_TOKEN>
META_OAUTH_REDIRECT_URI=https://plus.kachu.tw/meta/oauth/callback
META_OAUTH_SCOPES=pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish
```

2. 到 Meta App 後台確認 OAuth redirect allowlist 包含：

```text
https://plus.kachu.tw/meta/oauth/callback
```

3. 到 Meta App 後台確認 Webhook 設定：

```text
Callback URL: https://plus.kachu.tw/meta/webhook
Verify Token: <META_WEBHOOK_VERIFY_TOKEN>
```

4. 重新部署 `kachu-plus` 或至少重啟容器讓新 env 生效：

```bash
ssh root@172.234.85.159 'docker compose -f /opt/kachu-plus/docker-compose.prod.yml up -d --build kachu-plus'
```

5. 執行 migration，讓 `kachu_meta_oauth_sessions`、`kachu_content_plans`、`kachu_content_plan_items`、`kachu_external_engagements` 等 table 建立完成：

```bash
ssh root@172.234.85.159 'bash /opt/kachu-plus/infra/run_migration.sh'
```

6. 驗證 Meta 管理頁、OAuth callback 與 webhook verify：

```bash
curl -I https://plus.kachu.tw/tenants/<tenant_id>/meta/manage
curl -I 'https://plus.kachu.tw/meta/oauth/callback?state=dummy&error=test'
curl 'https://plus.kachu.tw/meta/webhook?hub.mode=subscribe&hub.verify_token=<META_WEBHOOK_VERIFY_TOKEN>&hub.challenge=12345'
```

預期結果：

- 第一個 URL 回 `200 OK`
- 第二個 URL 回 `409` 頁面或 `200` HTML 錯誤頁，代表 route 已上線
- 第三個 URL 直接回 `12345`，代表 Meta webhook verify 正常

---

## 常見問題

| 現象 | 可能原因 | 處理方式 |
|------|---------|---------|
| kachu-plus 容器 unhealthy | `.env.prod` 少填 key | `docker logs kachu-plus-kachu-plus-1` 確認錯誤 |
| AgentOS 啟動失敗 | agent_platform DB 未建立 | 確認 `/opt/kachu-plus/infra/init-db.sql` 有被執行（重建 postgres volume 後重啟） |
| SSL 申請失敗 | DNS 尚未生效 | `dig plus.kachu.tw` 確認 A record；最多等 5 分鐘後重試 `--skip-agentos-sync --skip-nginx` |
| nginx 設定已存在 | 重複部署 | `--skip-nginx` 跳過，或手動確認 `/opt/kachu-v2/infra/nginx/nginx.prod.conf` |
| LINE webhook 403 | channel_secret 不符 | Admin API 重新 patch tenant 的 channel_secret |

---

## Rollback

Kachu+ 與 v2 完全獨立，rollback 只需：

```bash
ssh root@172.234.85.159 'docker compose -f /opt/kachu-plus/docker-compose.prod.yml down'
```

v2 (`app.kachu.tw`) 不受影響。
