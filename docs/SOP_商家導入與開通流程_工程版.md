# Kachu+ 商家導入 SOP（工程版）
## 從平台準備到驗收交接的內部執行手冊

版本：v1.0  
更新日期：2026-05-09  
適用對象：工程師、Ops、內部支援人員

---

## 適用範圍

這份文件提供工程內部使用，涵蓋：
- 平台環境準備
- 商家 tenant 開通
- LINE webhook 設定
- Google / Meta 串接
- 驗收與排查

若你是 BD/CS，改看 [BD/CS 版 SOP](./SOP_商家導入與開通流程_BDCS版.md)。

若你現在正要收集正式開通所需的 secrets / payload，直接使用 [商家開通填值工作表](./商家開通填值工作表.md)。

## 階段一：平台準備

> 每台伺服器只需要執行一次，新商家加入時不需要重做。

### 1-A. 環境確認

| 項目 | 指令/說明 |
|---|---|
| Python 虛擬環境 | `source .venv/bin/activate` |
| PostgreSQL 可連線 | `psql $DATABASE_URL -c "\dt kachu*"` 確認主要資料表存在 |
| Alembic migration 最新 | `alembic upgrade head` |
| 環境變數 | `.env` 確認 `DATABASE_URL`、`LITELLM_MODEL`、`GOOGLE_AI_API_KEY`、`ADMIN_API_TOKEN` 已填寫；正式環境建議同時設定 `FIELD_ENCRYPTION_KEY` |

### 1-D. 開通前先準備填值資料

- 系統層 secrets 與 tenant / connector 欄位，請先整理在 [商家開通填值工作表](./商家開通填值工作表.md)
- Tenant 開通只吃 LINE + owner 資料
- Google / Meta connector 是 tenant 建好後分開呼叫的第二步

### 1-B. 服務啟動確認

```bash
uvicorn kachu_plus.main:create_app --factory --host 0.0.0.0 --port 8001
curl http://localhost:8001/docs
```

### 1-C. AgentOS 連線確認

```bash
curl $AGENTOS_BASE_URL/health
```

## 階段二：開通前需拿到的商家資料

| 欄位 | 說明 |
|---|---|
| 店名 | 商家顯示名稱 |
| 行業類型 | 中文描述即可 |
| 地址 | 實體地址或「網路」 |
| owner_line_user_id | 老闆本人 LINE user_id |
| line_channel_id | LINE Developers Console 取得 |
| line_channel_secret | LINE Developers Console 取得 |
| line_channel_access_token | Long-lived token |
| Google Business 是否串接 | 是 / 否 |
| Meta 是否串接 | 是 / 否 |

## 階段三：系統開通

> 目前版本使用 Admin API，一次 HTTP 呼叫建立 tenant + LINE config + owner membership。

### Step 1. 建立 Tenant 記錄

```bash
curl -X POST https://{YOUR_DOMAIN}/admin/tenants \
  -H "Authorization: Bearer {ADMIN_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "阿財滷味",
    "industry_type": "滷味小吃",
    "address": "台北市大安區復興南路一段 123 號",
    "owner_line_user_id": "U1a2b3c4d5e6f7g8h9i0j",
    "line_channel_id": "1234567890",
    "line_channel_secret": "abc123def456...",
    "line_channel_access_token": "長字串Token..."
  }'
```

回應範例：

```json
{
  "tenant_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "webhook_url": "https://{YOUR_DOMAIN}/webhooks/line/xxxxxxxx-...",
  "next_steps": [
    "1. 前往 LINE Developers Console，將 Webhook URL 設為：https://{YOUR_DOMAIN}/webhooks/line/{TENANT_ID}",
    "2. 確認 Use webhook 已勾選，點擊 Verify",
    "3. 關閉 LINE OA 的自動回覆訊息",
    "4. 通知商家老闆加入 LINE OA 好友"
  ]
}
```

說明：
- `ADMIN_API_TOKEN` 由環境變數設定。
- 若設定 `FIELD_ENCRYPTION_KEY`，`channel_secret` 與 `channel_access_token` 會以 Fernet 加密後存入 DB。

### Step 2. 設定 LINE Webhook URL

在 LINE Developers Console → Messaging API → Webhook settings 填入：

```text
https://{YOUR_DOMAIN}/webhooks/line/{TENANT_ID}
```

操作要求：
- 勾選 `Use webhook`
- 點擊 `Verify`
- 預期看到 `200 OK`

### Step 3. 關閉 LINE 自動回覆

在 LINE Official Account Manager → 回應設定：
- 關閉「自動回應訊息」
- 關閉「加入好友的歡迎訊息」

### Step 4. 記錄 Tenant ID

將 `tenant_id` 記錄在內部導入表單與商家卡片。

### Step 5. 驗證 Webhook 可用

```bash
curl -X POST https://{YOUR_DOMAIN}/webhooks/line/{TENANT_ID} \
  -H "Content-Type: application/json" \
  -H "X-Line-Signature: {計算簽章}" \
  -d '{"destination":"xxx","events":[]}'
```

## 階段四：商家加入 LINE 後的預期行為

- 商家加入好友後，系統會收到 `follow` event。
- Kachu+ 會立即推播 onboarding 歡迎訊息。
- 商家不需要先手動傳第一句話才開始。

## 階段五：Onboarding 後的外部平台串接

### 5-A. Google Business

目前完整 OAuth 流程未完成。暫行方式：

```bash
curl -X PUT https://{YOUR_DOMAIN}/tenants/{TENANT_ID}/google-business/connector \
  -H "Content-Type: application/json" \
  -d '{
    "account_label": "Google Business",
    "access_token": "ya29.xxx...",
    "account_id": "accounts/xxxxxxxxx",
    "location_id": "locations/xxxxxxxxx",
    "refresh_token": "1//0gxxxx...",
    "expires_at": 1760000000
  }'
```

### 5-B. Meta（FB/IG）

```bash
curl -X PUT https://{YOUR_DOMAIN}/tenants/{TENANT_ID}/meta/connector \
  -H "Content-Type: application/json" \
  -d '{
    "access_token": "EAAxxxxx...",
    "fb_page_id": "xxxxxxxx",
    "ig_user_id": "17841xxxxxx",
    "fb_access_token": "EAAxxxxx...",
    "expires_at": 1760000000
  }'
```

注意：
- Meta request field 是 `ig_user_id`，不是 `ig_account_id`
- 若 Facebook 與 Instagram 共用同一組 token，`fb_access_token` 可省略

## 階段六：驗收清單

### 工程驗收

| 項目 | 驗證方式 |
|---|---|
| Tenant 已建立 | `SELECT * FROM kachu_tenants WHERE id = '{tenant_id}';` |
| LINE channel config 已設定 | `SELECT * FROM kachu_line_channel_configs WHERE tenant_id = '{tenant_id}';` |
| Owner membership 已綁定 | `SELECT * FROM kachu_tenant_memberships WHERE tenant_id = '{tenant_id}';` |
| Onboarding 已完成 | `SELECT step FROM kachu_onboarding_states WHERE tenant_id = '{tenant_id}';` |
| Knowledge entries 已建立 | `SELECT COUNT(*) FROM kachu_knowledge_entries WHERE tenant_id = '{tenant_id}';` |
| Recurring jobs 可建立 | `SELECT * FROM kachu_recurring_jobs WHERE tenant_id = '{tenant_id}';` |

### 功能驗收

| 測試動作 | 預期結果 |
|---|---|
| 商家說「哪些客人超過 60 天沒來」 | 立即文字回覆 |
| 商家說「幫我寫一篇貼文」 | 先收到處理中，再收到草稿 |
| 商家說「幫我想一篇貼文」 | 直接收到諮詢建議 |
| 商家說「看一下最近有沒有評論要回」 | 進入評論回覆工作流 |

## 日常監控

```sql
SELECT tenant_id, workflow_type, status, created_at
FROM kachu_pending_approvals
ORDER BY created_at DESC LIMIT 20;

SELECT tenant_id, nudge_type, status, created_at
FROM kachu_suggestions
WHERE created_at > NOW() - INTERVAL '7 days';

SELECT tenant_id, job_type, next_run_at, last_run_at, last_run_status
FROM kachu_recurring_jobs
ORDER BY tenant_id, job_type;
```

## 常見排查

### 商家傳訊息沒有回應

1. 確認 Webhook URL 是否正確設定。
2. 確認 `kachu_line_channel_configs` 中 `tenant_id` 正確且 `is_active = true`。
3. 查看 log：`grep "tenant_id" logs/kachu.log | tail -20`。
4. 確認 signature 驗證沒有 mismatch。
5. 若由 Admin API 開通，確認 `ADMIN_API_TOKEN` 設定正確。

### Onboarding 卡住

```sql
SELECT step, updated_at FROM kachu_onboarding_states
WHERE tenant_id = '{tenant_id}';
```

### 主動建議沒有推播

1. 確認 `kachu_customer_profiles` 已有資料。
2. 確認 `sleep_threshold` 設定合理。
3. 確認 `LINE_CHANNEL_ACCESS_TOKEN` 未過期。
4. 檢查 `kachu_recurring_jobs.next_run_at` 是否異常。

### Google 評論抓取失敗

1. 確認 `kachu_connector_accounts` 中 Google connector 為 active。
2. 確認 access_token 未過期。
3. 確認 `account_id` 與 `location_id` 格式正確。
4. 若沒有 `refresh_token`，token 過期後只能人工重填。

## 已知限制

### 1. Admin Web UI 尚未實作

- 已有 Admin API，但仍無完整後台畫面。

### 2. Google / Meta OAuth 尚未完整自助化

- 目前仍需工程配合設定 token。

### 3. Recurring Job 仍為 In-Process Scheduler

- 伺服器重啟時，in-process sleep 不會保留。
- DB 中的 pending approvals 與 next_run_at 仍會保留。

---

*本文件供工程內部使用。對商家或 BD/CS 的說明，請改看 BD/CS 版。*
