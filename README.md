# Kachu v2

Kachu v2 是一套面向微型商家與個人品牌經營者的 AI 營運助理產品。

目前產品仍維持三個核心原則：

- LINE 是主要操作介面
- 對外動作預設要先經過老闆確認
- AgentOS 是執行基礎設施，不是產品本身

## 專案現況

截至 2026-05-03，Kachu 已從單租戶原型推進到可持續運作的多租戶 beta 系統。

這一輪已完成的關鍵收尾包括：

- 多租戶 membership-first runtime 已落地，正式 routing 以 tenant membership 為主，`LINE_BOSS_USER_ID` 只保留 legacy / local-dev fallback
- owner / manager 權限分流已擴到更多控制面，不只 approval postback，也包含 connector reconnect / disconnect 與 tenant 營運操作
- dashboard 已具備 tenant settings、connector refresh / disconnect、tenant health、tenant export、tenant deactivate、tenant delete 等 SaaS 營運能力
- 知識庫 lifecycle 已補成可營運控制：dashboard 可看 Active / Stale / Conflict 摘要，支援手動標記、恢復與 stale 掃描
- production 啟動已切到 migration-only startup；在 production 且 `ALLOW_SCHEMA_CREATE_IN_PRODUCTION=false` 時，會先驗證 schema 已跟上 migration，再以 `create_schema=False` 啟動
- Google OAuth、Meta OAuth、GA4、排程發文、知識庫、FAQ escalation、approval gate、audit log 等高頻產品路徑已都有對應測試覆蓋
- dashboard auth 已收斂成：HTML bootstrap 可接受 query token，但後續 API 一律要求 `Authorization: Bearer <token>`

目前整體定位是：

- 不是 PoC
- 不是一次性 demo
- 是可運行的早期 beta
- 距離穩定商用仍有工程化與營運面的收尾工作

## 已落地能力

### 1. 老闆操作與 AI 協作

- 老闆可透過 LINE 下達內容、營運、知識更新、報表與一般諮詢指令
- 內容草稿、評論回覆與部分營運動作都可先經過 approval gate 再執行
- 老闆自由對話會持久化，並折疊進 `owner_brief` 與 `brand_brief`，供後續 workflow 使用
- 一般自由對話會透過 `BusinessConsultant` 結合知識庫、產業脈絡、近況記憶與 GA 建議做回覆

### 2. 多租戶 SaaS 能力

- 每個 tenant 有獨立 membership、connector、automation settings、feature flags、shared context 與 audit records
- owner / manager 權限已分流
- dashboard 所有 API 均要求顯式 `tenant_id`
- 舊單租戶資料已補 migration 與 backfill repair，可銜接到新 membership-first runtime

### 3. Dashboard 管理後台

- 系統總覽、工作流執行紀錄、待審批任務、知識庫管理
- 知識條目狀態管理與 lifecycle 掃描（active / stale / conflict / superseded / archived）
- 連接器狀態與強制 refresh token
- tenant plan、feature flags 與 capability 檢視
- tenant health snapshot
- LINE 推播紀錄與 workflow audit
- tenant export / deactivate / delete 等營運操作
- tenant-level automation settings：GA 報表、Google / Meta 發文、主動提醒、content calendar、timezone

### 4. 通路與工作流

- Google Business Profile OAuth per-tenant 串接、發文、評論通知、評論回覆
- Meta OAuth、Facebook / Instagram 發文
- GA4 指標抓取、期間比較、top channel / landing page breakdown 與異常導向摘要
- 照片內容生成與 approval 發布
- 顧客 LINE FAQ 自動回覆與 escalation
- tenant-configurable scheduler 與延後重試佇列

## 尚未完成或仍有缺口的部分

這些不是多租戶未完成，而是產品 roadmap 或工程化仍在持續中的項目：

- 官網 / Blog 發文整合尚未實作
- Facebook 評論 / 留言 webhook 與 AI 代回尚未完成
- CI/CD 尚未建立
- 尚未接入固定的 lint / type-check gate
- LangGraph、Qdrant 仍存在文件與實作落差，尚未成為主路徑
- AgentOS 依然是關鍵執行層依賴

## 技術架構摘要

### 核心組件

- FastAPI：API 與 webhook 主服務
- SQLModel / SQLAlchemy：資料存取與 repository 層
- Alembic：schema migration 管理
- AgentOS：workflow / task / approval 執行基礎設施
- APScheduler：tenant-aware automation dispatch
- LINE / Google / Meta / GA4 client：外部通路整合

### 主要執行模型

- LINE webhook 進入後先解析 tenant 與角色，再分流到 onboarding、consult、workflow dispatch 或 postback action
- workflow 需要對外動作時，會經由 approval gate 或指定工具路由執行
- context brief、shared context、structured knowledge 與 episodic memory 會被用來補強後續生成與建議
- dashboard 是明確的 admin / ops 介面，不是主要產品入口

### Production schema 策略

- development / test 可建立 schema
- production 預設不允許自動 `create_all()`
- production 啟動前應先完成 `alembic upgrade head`
- 若 migration 未跟上，app 會在啟動階段直接阻擋，而不是默默自動補 schema

## Google Business Profile（GBP）多租戶架構

Kachu 採用 OAuth per-tenant，而不是用單一 service account 管全部商家。

正確流程如下：

```text
客戶開啟 /auth/google/connect?tenant_id=<tenant>
  -> Google OAuth consent screen
  -> /auth/google/callback
  -> 取得 access_token + refresh_token
  -> 自動 discovery account_id / location_id
  -> 存入該 tenant 的 connector_account
  -> 後續發文 / 讀評論 / 回評論都從 DB 取該 tenant 的憑證
```

`connector_account.credentials_encrypted`（`platform="google_business"`）內會保存：

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

token 到期前 5 分鐘內，系統會自動使用 `refresh_token` 更新 access token，並回寫 DB。

`GOOGLE_SERVICE_ACCOUNT_JSON`、`GOOGLE_BUSINESS_ACCOUNT_ID`、`GOOGLE_BUSINESS_LOCATION_ID` 只保留作為 legacy / 測試 fallback，不是 SaaS 正式路徑。

## 工作區結構

- `src/kachu/`：Kachu 產品主程式、router、memory、scheduler、dashboard、整合 client
- `tests/`：產品與整合測試
- `alembic/`：migration 設定與 revisions
- `scripts/release_check.py`：release gate 單一入口
- `scripts/smoke_phase6.py`：in-process smoke test
- `scripts/deploy_phase6_prod.py`：production deploy helper
- `docs/`：runbook、debug playbook、功能比較、契約矩陣與產品現況文件

目前 migration head 為：

- `20260427_0001_baseline`
- `20260430_0002_automation_and_briefs`
- `20260502_0003_meta_post_automation_settings`
- `20260502_0004_line_scheduled_publishes`
- `20260503_0005_tenant_memberships`
- `20260503_0006_tenant_feature_flags`
- `20260503_0007_repair_legacy_membership_backfill`
- `20260503_0008_knowledge_lifecycle_metadata`

## 本機開發

建議使用工作區內的 `.venv311`。

### 安裝依賴

```bash
pip install -e .[dev]
```

### 啟動服務

```bash
uvicorn kachu.main:create_app --factory --app-dir src --reload
```

### 套用 migration

```bash
alembic upgrade head
```

### 執行測試

```bash
.venv311/bin/python -m pytest
```

常用本機指令：

```bash
.venv311/bin/python -m pytest tests/ -q
.venv311/bin/python -m pytest tests/test_phase5_features.py -q
.venv311/bin/python -m pytest tests/test_google_oauth_discovery.py -q
uvicorn kachu.main:create_app --factory --app-dir src --reload
```

dashboard 在 test mode 以外需要設定 `ADMIN_SERVICE_TOKEN`。
API 存取請使用：

```text
Authorization: Bearer <ADMIN_SERVICE_TOKEN>
```

如果是 browser bootstrap，可透過 `/dashboard?token=<ADMIN_SERVICE_TOKEN>&tenant_id=<tenant>` 載入頁面；頁面初始化後會把 token 從 URL 移除，後續 API 改走 Bearer header。

## 測試與 release gate

截至 2026-05-03，完整測試套件結果為：

```bash
.venv311/bin/python -m pytest
```

結果：`285 passed`

Phase 6 release gate 單一入口：

```bash
python scripts/release_check.py
```

它會執行：

- Kachu automated tests
- AgentOS automated tests
- in-process smoke test（含 temporary tenant seed + cleanup）

production rollout 可使用：

```bash
python scripts/deploy_phase6_prod.py --host root@your-server
```

release 的基本順序應為：

1. 同步 Kachu 與 AgentOS source tree 到遠端
2. 跑 release gate
3. build images
4. 套用 migration
5. 啟動或重啟服務
6. 執行 smoke validation
7. 確認後才視為完成

## Production 部署重點

- 目標模式是單台 Linux 主機，搭配 Docker Compose、nginx、Let's Encrypt
- production 啟動前先確認 `.env.prod` 與必要密鑰已正確配置
- LINE webhook URL 應指向 `/webhooks/line`
- healthy 只代表容器活著，不代表 workflow 一定可用
- production smoke 應在 container 內或 temporary tenant 上完成，避免真實 LINE push 或不可逆副作用

## 值得先讀的文件

- `docs/kachu-current-assessment-2026-04-27.md`：系統現況總評
- `docs/tech-debt-and-issues.md`：已知問題與技術債
- `docs/feature-comparison.md`：理想需求與現況對照
- `docs/release-runbook.md`：release gate 與 smoke 契約
- `docs/deploy-runbook.md`：production 部署流程
- `docs/debug-playbook.md`：production 問題排查路徑
- `docs/boundary-contract.md`：Kachu / AgentOS 職責邊界
- `docs/contract-test-matrix.md`：重要契約測試覆蓋面

## 值得先看的程式碼

- `src/kachu/main.py`：app factory 與啟動流程
- `src/kachu/line/webhook.py`：LINE webhook 與 boss / customer routing
- `src/kachu/dashboard/router.py`：dashboard API 與 tenant ops
- `src/kachu/static/dashboard.html`：dashboard 前端
- `src/kachu/tools/router.py`：主要 workflow tool endpoints
- `src/kachu/auth/oauth.py`：Google / Meta OAuth 流程
- `src/kachu/scheduler.py`：tenant-aware automation dispatch
- `src/kachu/persistence/repository.py`：資料層與 dashboard 聚合查詢

## 專案意圖

Kachu v2 的重點不是再堆更多 agent 花樣。

這個版本真正要做的是把產品行為收斂成可持續維護、可部署、可追查的系統：

- release checks 可重複
- 高頻路徑有契約測試保護
- Kachu / AgentOS 邊界清楚
- production 問題可以依 `tenant_id`、`task_id`、`run_id`、`audit event` 往回追

如果接下來再補上 CI、靜態品質檢查與剩餘通路缺口，Kachu 就會從可運行的 beta 更接近穩定商用候選版本。