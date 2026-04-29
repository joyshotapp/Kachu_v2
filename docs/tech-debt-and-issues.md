# Kachu v2 — 技術負債與問題清單（複查後）

> 更新日期：2026-04-27  
> 此文件記錄程式碼審查後仍成立的負面問題，以及已完成修復的項目。  
> 狀態：`open` = 尚未修復，`partial` = 部分修復，`resolved` = 已修復  
> 嚴重度：`Critical / High / Medium / Low`

---

## 一、目前仍成立的問題

### [SEC-1] 工作區存在生產樣式憑證檔，且忽略規則原本不足

- 狀態：`partial`
- 嚴重度：`Critical`
- 檔案：[.env.prod](c:\Users\User\Desktop\Kachu-v2\.env.prod)

`.env.prod` 內含真實樣式的敏感資訊，包括 LINE token、OpenAI key、Google AI key、資料庫密碼。這代表工作區本身已有高風險憑證暴露面。

已完成修復：
- [.gitignore](c:\Users\User\Desktop\Kachu-v2\.gitignore#L1) 已補上 `.env.*`，並保留 `.env.example`。

仍待處理：
- 目前工作區沒有 `.git`，無法確認 `.env.prod` 是否曾經提交到版本控制歷史。
- 無法確認這些 key 是否已撤銷或輪替。

建議：
- 立即輪替 `.env.prod` 內所有實際可用的密鑰與密碼。
- 若這個檔案曾被 commit，需另外清理 Git 歷史。

---

### [SEC-2] Google review webhook 已支援 OIDC，但部署上仍需完成正式配置

- 狀態：`partial`
- 嚴重度：`High`
- 檔案：[src/kachu/google/webhook.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\google\webhook.py#L103)

目前程式碼已支援兩種驗證路徑：
- shared secret bearer token
- Google OIDC token 驗證（issuer / audience / service account email）

但這仍屬於 `partial`，因為是否真的以 OIDC 上線，取決於部署端是否有設定對應 audience 與 service account email。

建議：
- 下一步改成驗證 Google Pub/Sub push 的 OIDC token。
- 至少要驗 issuer、audience、service account subject。

---

### [SEC-3] Production 設定驗證已接上，且條件式驗證已補強

- 狀態：`partial`
- 嚴重度：`High`
- 檔案：[src/kachu/config.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\config.py#L142)

目前 `create_app()` 已會呼叫 `settings.validate_production_config()`，因此：
- `SECRET_KEY`
- `TOKEN_ENCRYPTION_KEY`
- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `GOOGLE_AI_API_KEY or OPENAI_API_KEY`

這些欄位在 production 缺漏時會直接阻止啟動。

目前已新增條件式驗證：
- `FEATURE_META=True` 時，要求 `META_APP_ID` + `META_APP_SECRET`
- 設定 `NEWEBPAY_MERCHANT_ID` 時，要求 `NEWEBPAY_HASH_KEY` + `NEWEBPAY_HASH_IV`
- 使用 `ADMIN_EMAIL` / `ADMIN_PASSWORD` 時，要求兩者成對存在

仍待處理：
- `ADMIN_SERVICE_TOKEN` 路徑尚未做更細的條件驗證
- 部分 feature flag 的配置契約仍未完整明文化

---

### [ARCH-1] LangGraph 仍停留在產品規劃，未出現在實作主路徑

- 狀態：`open`
- 嚴重度：`Medium`
- 檔案：[Kachu-v2-Product-Plan.md](c:\Users\User\Desktop\Kachu-v2\Kachu-v2-Product-Plan.md), [src/kachu/tools/router.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\tools\router.py)

產品計畫將 LangGraph 描述為認知工作流核心，但目前主要邏輯仍是 FastAPI tool endpoints 直接呼叫 LLM。這屬於文件與實作落差，不是單純缺少套件。

建議：
- 二選一：
  1. 真的導入 LangGraph，讓文件與架構一致。
  2. 把 Product Plan 改寫成目前真實架構，移除未落地敘述。

---

### [ARCH-2] Qdrant 仍未進入主要檢索路徑

- 狀態：`open`
- 嚴重度：`Medium`
- 檔案：[Kachu-v2-Product-Plan.md](c:\Users\User\Desktop\Kachu-v2\Kachu-v2-Product-Plan.md), [src/kachu/memory/vector_search.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\memory\vector_search.py)

目前主路徑仍使用 in-process cosine similarity，而不是 Qdrant 查詢。這同樣是架構落差，不是單純依賴缺失。

建議：
- 若短期不導入 Qdrant，應修正文檔與命名，避免誤導為 production-ready hybrid retrieval。
- 若要導入，應補 repository / retriever 邊界，讓 Qdrant 成為主查詢來源。

---

### [ARCH-3] AgentOS 仍是執行層單點依賴

- 狀態：`open`
- 嚴重度：`Medium`
- 檔案：[src/kachu/agentOS_client.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\agentOS_client.py)

Task、Run、Approval、Retry、Idempotency 全部依賴 AgentOS。若 AgentOS 不可用，Kachu 幾乎無降級能力。

建議：
- 至少補上明確錯誤分類與 retry / circuit breaker 策略。
- 對關鍵 webhook 路徑加 audit log，避免失敗時靜默丟失事件。

---

### [ARCH-4] 資料庫 schema 仍缺正式 migration 流程

- 狀態：`partial`
- 嚴重度：`Medium`
- 檔案：[src/kachu/persistence/db.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\persistence\db.py#L22)

目前仍沒有 Alembic migration 腳本，這對 production schema 演進仍是風險。

已完成修復：
- production 啟動現在預設禁止自動 `create_all()`，會要求先跑 migration 或明確 opt-in。
- 已新增 [alembic.ini](c:\Users\User\Desktop\Kachu-v2\alembic.ini)、[alembic/env.py](c:\Users\User\Desktop\Kachu-v2\alembic\env.py) 與 baseline revision [20260427_0001_baseline.py](c:\Users\User\Desktop\Kachu-v2\alembic\versions\20260427_0001_baseline.py)。

建議：
- 補上正式 migration runner，再完全移除 production opt-in escape hatch。
- 在實際資料庫執行 baseline migration，確認升降版流程可用。

---

### [ARCH-5] Langfuse 仍直接讀取環境變數，未完全納入 Settings 管理

- 狀態：`open`
- 嚴重度：`Medium`
- 檔案：[src/kachu/llm/client.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\llm\client.py#L15)

雖然 LLM provider API key 的全域 `os.environ` 污染已修正，但 Langfuse client 仍透過 `os.getenv()` 和 `os.environ[]` 讀取設定，未與 app `Settings` 完全整合。

建議：
- 在 app 啟動時以 `Settings` 建立 Langfuse client，注入 app state 或 service layer。

---

### [CODE-2] `tools/router.py` 已有 helper，但部分 JSON fence 清理邏輯仍重複

- 狀態：`open`
- 嚴重度：`Low`
- 檔案：[src/kachu/tools/router.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\tools\router.py)

`_strip_json_fence()` 已存在，但部分路徑仍手寫同類清理邏輯。這不是功能性 bug，但會增加維護成本。

---

### [CODE-3] 仍缺自動化 lint / type check 流程

- 狀態：`open`
- 嚴重度：`Low`
- 檔案：[pyproject.toml](c:\Users\User\Desktop\Kachu-v2\pyproject.toml)

目前仍未納入 `ruff`、`mypy` 或 `pyright` 等靜態品質工具。

建議：
- 先加 `ruff`，再視團隊接受度補 `mypy` 或 `pyright`。

---

### [TEST-1] 完整 runtime 測試環境尚未建立

- 狀態：`partial`
- 嚴重度：`Low`
- 檔案：[tests/test_security_guards.py](c:\Users\User\Desktop\Kachu-v2\tests\test_security_guards.py)

目前專案 `.venv` 已可安裝依賴並執行 focused pytest；本次已實際跑過多組回歸測試。但整體仍未形成完整、可在 CI 重現的標準化 runtime 驗證流程。

本次已完成：
- focused pytest 可於現有 `.venv` 執行
- 已通過 `tests/test_security_guards.py`
- 已通過 `tests/test_line_webhook_resilience.py`
- 已通過 `tests/test_edit_session_to_publish.py`
- 已通過 `tests/test_publish_content_with_meta.py`
- 已通過 `tests/test_photo_content_e2e.py`
- 已通過 `tests/test_onboarding_document_ingestion.py`
- 已通過 `tests/test_phase2_workflows.py`
- 已通過 `tests/test_phase5_features.py`

建議：
- 將目前 `.venv` 安裝與 pytest 指令整理成固定 runbook 或 task。
- 下一步把 focused suite 接進 CI，而不是只靠本機執行。

---

### [OPS-1] 仍未建立 CI/CD

- 狀態：`open`
- 嚴重度：`Low`
- 檔案：專案層級

目前仍沒有 GitHub Actions 或其他 CI 配置，自動測試與靜態檢查都未串接。

---

## 二、已完成修復的項目

### [FIXED-1] Production 設定驗證已接入啟動流程

- 狀態：`resolved`
- 檔案：[src/kachu/main.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\main.py#L26), [src/kachu/config.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\config.py#L142)

原本 `validate_production_config()` 存在但沒被呼叫，現在 app 啟動時會執行驗證。

---

### [FIXED-2] LINE webhook 已不再因空 secret 而靜默跳過驗簽

- 狀態：`resolved`
- 檔案：[src/kachu/line/webhook.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\line\webhook.py#L66)

非 test 環境下，若缺 `LINE_CHANNEL_SECRET` 會直接回 `503`，避免未簽名請求被接受。

---

### [FIXED-3] Google webhook 已加授權檢查，且取消對所有租戶廣播

- 狀態：`resolved`
- 檔案：[src/kachu/google/webhook.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\google\webhook.py#L53), [src/kachu/google/webhook.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\google\webhook.py#L151), [src/kachu/persistence/repository.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\persistence\repository.py#L486)

現在 webhook 只會在授權通過且 location 能映射到 tenant 時才觸發 workflow，否則直接忽略。

---

### [FIXED-4] `analyze-photo` 已改為顯式 degraded fallback

- 狀態：`resolved`
- 檔案：[src/kachu/tools/router.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\tools\router.py#L52), [src/kachu/tools/router.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\tools\router.py#L101)

當影像分析未啟用、缺少 `photo_url` 或 Gemini Vision 呼叫失敗時，現在會回傳 `status="degraded"`、`needs_manual_review=True` 與錯誤代碼，不再偽裝成成功分析結果。

---

### [FIXED-5] LLM provider key 不再寫入全域 `os.environ`

- 狀態：`resolved`
- 檔案：[src/kachu/llm/client.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\llm\client.py#L121)

避免 async 請求之間互相污染 provider API key。

---

### [FIXED-6] `ApprovalBridge` 型別、返回值與格式問題已清理

- 狀態：`resolved`
- 檔案：[src/kachu/approval_bridge.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\approval_bridge.py)

`__init__` 改為 `-> None`，不再在 `-> None` 函式中 `return False`，並已清除主要空行與亂碼註解問題。

---

### [FIXED-7] `.env.example` 與實際設定名稱已對齊

- 狀態：`resolved`
- 檔案：[.env.example](c:\Users\User\Desktop\Kachu-v2\.env.example#L19), [.env.example](c:\Users\User\Desktop\Kachu-v2\.env.example#L30)

已將 `GEMINI_API_KEY` 對齊為 `GOOGLE_AI_API_KEY`，並將 `KACHU_ENV` 對齊為 `APP_ENV`。

---

### [FIXED-8] 重複 `base64` import 已移除

- 狀態：`resolved`
- 檔案：[src/kachu/line/webhook.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\line\webhook.py)

---

### [FIXED-9] `tools/router.py` 已把可降級錯誤與系統級異常分流

- 狀態：`resolved`
- 檔案：[src/kachu/tools/router.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\tools\router.py)

已新增 recoverable helper，將 LLM/provider 失敗與 JSON 解析失敗收斂到可降級路徑；對於不屬於外部服務/格式層的非預期異常，現在會重新拋出，不再一律吞成 stub 或 degraded 回應。

---

### [FIXED-10] LINE webhook 已補下載 retry 與明確失敗通知

- 狀態：`resolved`
- 檔案：[src/kachu/line/webhook.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\line\webhook.py)

圖片/檔案下載現在會對 timeout、429、5xx 做有限次 retry；最終失敗時會主動推送明確訊息給 LINE 使用者，而不是只寫 log 後繼續走後續流程。另已修正 GoalParser 路徑中被吞掉的推播參數錯誤。

---

### [FIXED-11] OAuth state store 已支援 Redis 共享儲存

- 狀態：`resolved`
- 檔案：[src/kachu/auth/oauth.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\auth\oauth.py), [src/kachu/config.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\config.py), [pyproject.toml](c:\Users\User\Desktop\Kachu-v2\pyproject.toml)

OAuth state 在 production/Redis backend 下現在會寫入 Redis 並使用 TTL，自動避免多實例部署時 state 不共享的問題；dev/test 仍保留 memory fallback。另已補 `OAUTH_STATE_STORE_BACKEND` 與 `OAUTH_STATE_TTL_SECONDS` 設定，並禁止 production 使用 memory backend。

---

### [FIXED-12] `ApprovalBridge` 已收窄例外處理並修正編輯審批成功訊號

- 狀態：`resolved`
- 檔案：[src/kachu/approval_bridge.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\approval_bridge.py), [tests/test_edit_session_to_publish.py](c:\Users\User\Desktop\Kachu-v2\tests\test_edit_session_to_publish.py)

`ApprovalBridge` 現在只對可恢復的 AgentOS / LINE / DB 例外做局部處理，不再以 broad exception 吞掉所有錯誤；同時 `complete_edit_and_approve()` 已改為明確回傳 `bool`，修正呼叫端原本無法正確判斷編輯審批是否成功的問題。

---

### [FIXED-13] `document_parser.py` 已將可恢復解析錯誤與非預期 bug 分流

- 狀態：`resolved`
- 檔案：[src/kachu/document_parser.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\document_parser.py), [tests/test_onboarding_document_ingestion.py](c:\Users\User\Desktop\Kachu-v2\tests\test_onboarding_document_ingestion.py)

`parse_document()` 現在只對 parser/provider 層的可恢復錯誤回傳 `needs_manual` 結果，不再用 broad exception 混吞所有異常；JSON block 解析與 text fallback decode 也已改成具體例外處理。對於真正的程式錯誤，現在會直接冒泡，避免 onboarding 路徑把 bug 偽裝成一般解析失敗。

---

### [FIXED-14] `IntentRouter` / `GoalParser` 已收窄 LLM fallback 與 dispatch 例外處理

- 狀態：`resolved`
- 檔案：[src/kachu/intent_router.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\intent_router.py), [src/kachu/goal_parser.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\goal_parser.py), [tests/test_phase2_workflows.py](c:\Users\User\Desktop\Kachu-v2\tests\test_phase2_workflows.py), [tests/test_phase5_features.py](c:\Users\User\Desktop\Kachu-v2\tests\test_phase5_features.py)

LLM classification 現在只會在可恢復錯誤時 fallback 到 keyword / default domain；像 `AssertionError` 這類真正的程式 bug 會直接冒泡。`IntentRouter._create_and_run()` 也已改成只處理 AgentOS / validation / DB 這類明確可恢復故障，不再以 broad exception 吞掉所有 dispatch 失敗。

---

### [FIXED-15] 排程面 `scheduler` / `proactive_monitor` / `content_calendar` 已收窄 broad exception

- 狀態：`resolved`
- 檔案：[src/kachu/scheduler.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\scheduler.py), [src/kachu/proactive_monitor.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\proactive_monitor.py), [src/kachu/content_calendar.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\content_calendar.py), [tests/test_phase4_policy.py](c:\Users\User\Desktop\Kachu-v2\tests\test_phase4_policy.py), [tests/test_phase5_features.py](c:\Users\User\Desktop\Kachu-v2\tests\test_phase5_features.py)

排程相關元件現在只會吞明確可恢復的 AgentOS / LINE / LLM / DB 類錯誤，並保留對非預期程式錯誤的冒泡行為。這讓定時工作仍能對 timeout、validation 或 SQL 層故障做局部記錄與繼續，但不再把真正 bug 混成一般排程失敗。相對應的 recoverable / unexpected 回歸測試也已補上。

---

### [FIXED-16] `policy.py` 已將 approval profile fallback 收斂為 DB 錯誤

- 狀態：`resolved`
- 檔案：[src/kachu/policy.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\policy.py), [tests/test_phase4_policy.py](c:\Users\User\Desktop\Kachu-v2\tests\test_phase4_policy.py)

`KachuExecutionPolicyResolver.resolve()` 現在只會在 approval profile 載入遇到資料庫層錯誤時回傳 `error_fallback`；像 `AssertionError` 這種非預期程式錯誤會直接冒泡，不再被 broad exception 吞掉。相對應的 DB fallback 與 unexpected-error regression test 已補上。

---

### [FIXED-17] 全域 broad exception 已縮到只剩刻意保留的 helper wrapper

- 狀態：`resolved`
- 檔案：[src/kachu/auth/oauth.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\auth\oauth.py), [src/kachu/dashboard/router.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\dashboard\router.py), [src/kachu/memory/embedder.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\memory\embedder.py), [src/kachu/memory/manager.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\memory\manager.py), [src/kachu/persistence/repository.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\persistence\repository.py), [tests/test_memory.py](c:\Users\User\Desktop\Kachu-v2\tests\test_memory.py), [tests/test_phase6_audit.py](c:\Users\User\Desktop\Kachu-v2\tests\test_phase6_audit.py), [tests/test_phase2_workflows.py](c:\Users\User\Desktop\Kachu-v2\tests\test_phase2_workflows.py)

這一輪把 OAuth Meta discovery、dashboard JSON decode、memory embedding / decode fallback，以及 shared context JSON decode 這批 broad exception 全部收窄成具體的 HTTP / JSON / 型別錯誤。全域掃描後，`src/kachu/**/*.py` 目前只剩 3 個 `except Exception`，而且都屬於刻意保留的 helper wrapper：兩個在 `tools/router.py`，一個在 `llm/client.py`。

---

## 三、修復優先順序（更新版）

```text
立即：
  1. 輪替 .env.prod 內所有真實可用憑證
  2. 在部署環境完成 Google webhook OIDC audience / service account 設定

本週：
  3. 建立 Alembic baseline migration
  4. 補完整 `ADMIN_SERVICE_TOKEN` 等剩餘設定契約

下個 Sprint：
  5. 對齊 Product Plan 與真實架構（LangGraph / Qdrant）
  6. 補上 lint/type check 與 CI
  7. 建立可重現的 pytest 執行環境
```
