# Kachu v2

Kachu v2 is an agent-native AI operations assistant for micro-business owners.

The product remains centered on three rules:
- LINE is the main operator surface.
- The boss confirms external actions before they happen.
- AgentOS is runtime infrastructure, not the product itself.

## Workspace layout

- `src/kachu/` — Kachu product logic, adapters, memory, router, webhook, scheduler
- `tests/` — Kachu product and integration tests
- `scripts/release_check.py` — one entry point for Phase 6 release validation
- `scripts/smoke_phase6.py` — in-process smoke test with temporary tenant seeding + cleanup
- `scripts/deploy_phase6_prod.py` — explicit release-check -> build -> up -> smoke production helper
- `docs/boundary-contract.md` — Kachu / AgentOS responsibility boundary
- `docs/contract-test-matrix.md` — workflow boundary coverage map
- `docs/release-runbook.md` — release gate and smoke sequence
- `docs/debug-playbook.md` — production debugging path by symptom and ID

## Local development

1. Install dependencies with `pip install -e .[dev]`.
2. Run the app with `uvicorn kachu.main:app --app-dir src --reload`.
3. Run tests with `python -m pytest`.

## Phase 6 release gate

Run the full Phase 6 gate from the Kachu workspace root:

```bash
python scripts/release_check.py
```

This runs:
- Kachu tests
- AgentOS tests
- Phase 6 in-process smoke test

Optional flags:
- `--skip-kachu-tests`
- `--skip-agentos-tests`
- `--skip-smoke`

## Phase 6 intent

Phase 6 is not about adding more agent tricks.
It is about making Kachu behave like a stable product:
- release checks are repeatable
- high-frequency paths stay contract-tested
- Kachu / AgentOS boundaries stay explicit
- production issues can be traced from `task_id` and `run_id`

## Google Business Profile (GBP) 整合架構

### 設計決策（一次說清楚，不要再花時間釐清）

Kachu 是 **SaaS 多租戶** 產品。每個客戶各自授權自己的 Google 帳號，Kachu 代為操作。  
**不使用 Service Account 統一管理所有商家**，那是 single-tenant 的做法，對 SaaS 沒有意義。

### 正確流程（路徑 B — OAuth per-tenant）

```
客戶點連結 GET /auth/google/connect?tenant_id=XXX
    ↓
Google OAuth consent screen（business.manage scope）
    ↓
GET /auth/google/callback
    → 取得 access_token + refresh_token
    → 自動呼叫 GBP API 取得 account_id（accounts/XXXXXXX）和 location_id
    → 一起存入 ConnectorAccountTable（platform="google_business"）
    ↓
後續所有 GBP API 呼叫（發文、讀評論、回評論）
    → _get_gbp_creds(repo, tenant_id, settings) 從 DB 取出 token + account/location ID
    → GoogleBusinessClient.from_oauth_token(access_token) 呼叫 API
```

### Credential 存放格式

`connector_account.credentials_encrypted`（platform=`google_business`）JSON 結構：
```json
{
  "access_token": "ya29.xxx",
  "refresh_token": "1//xxx",
  "expires_in": 3600,
  "expires_at": 1746012345,
  "scope": "https://www.googleapis.com/auth/business.manage",
  "token_type": "Bearer",
  "account_id": "accounts/123456789",
  "location_id": "accounts/123456789/locations/987654321"
}
```

`expires_at` 是 Unix timestamp（秒）。`_get_gbp_creds()` 會在 token 到期前 5 分鐘自動用 `refresh_token` 換新，並更新 DB。

### Service Account fallback（路徑 A）

`_get_gbp_creds()` 在 DB 找不到 per-tenant token 時，會退回讀 env var：
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_BUSINESS_ACCOUNT_ID`
- `GOOGLE_BUSINESS_LOCATION_ID`

這是 legacy / 測試用途，**不是 SaaS 客戶使用的路徑**。

### GCP 設定需求（OAuth consent screen）

- OAuth scope `https://www.googleapis.com/auth/business.manage` 是 **restricted scope**
- **2026-04-30 已點「發布應用程式」，consent screen 狀態：`實際運作中`（In Production）**
- 任何人都可以完成 OAuth 授權，但因 restricted scope 未通過 Google 驗證，授權時會出現「Google 尚未驗證這個應用程式」警告
  - 用戶點「進階 → 繼續前往」仍可完成授權，功能正常
  - 若要移除警告需申請 CASA Tier 2 安全評估（約 USD $75–150，需數週至數個月）
- GCP project: `opsly-492412`，OAuth client 設定在 `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`
- GCP account: `y.c.chen1112@gmail.com`（Steve Chen）

### Token Refresh 機制

`_get_gbp_creds()` 每次被呼叫時自動檢查 token 是否即將過期：
1. 若 `time.time() > expires_at - 300`（到期前 5 分鐘內）
2. 用 `refresh_token` 呼叫 `https://oauth2.googleapis.com/token`
3. 成功則更新 DB（`save_connector_account`）並使用新 token
4. 失敗則 warning log，繼續用現有 token（不 crash）

此機制確保長時間運行的租戶不需重新授權。

### 相關程式碼位置

| 功能 | 檔案 |
|------|------|
| OAuth 授權入口 + callback | `src/kachu/auth/oauth.py` |
| GBP API client | `src/kachu/google/business_client.py` |
| Credential 解析 helper | `tools/router.py` → `_get_gbp_creds()` |
| Credential 存取 DB | `persistence/repository.py` → `save/get_connector_account()` |