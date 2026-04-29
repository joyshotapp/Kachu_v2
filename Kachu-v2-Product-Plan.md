# Kachu v2 — Agent-Native 產品計畫
**版本：** v2.0  
**日期：** 2026-04-26  
**定位：** 微型創業者的 AI 數位營運幕僚 — 以 AgentOS 為執行平台從 0 打造

---

## 一、最高指導原則

> **本質是 agent 的產品。**

所有設計決策從這個原則出發：
- 老闆的主要關係是跟 **Agent**，不是跟介面
- 每個決策問：「這讓 Agent 能更好地替老闆做事嗎？」
- LINE 是主要入口，Dashboard 是次要的監控工具
- 老闆不需要學任何東西，Agent 主動做，老闆只需確認

---

## 二、Kachu v1 的問題

Kachu v1 的設計思想本身已是 agent-native（LangGraph 認知 + Temporal 耐久執行 + LINE 對話入口），方向正確。

**根本問題是：Kachu 自己管理所有執行基礎設施。**

```
Kachu v1:
老闆 LINE → Kachu → Temporal（自維護）→ LangGraph → 外部平台
                        ↑
            Kachu 自己的 ApprovalService 橋接 LINE postback → Temporal Signal
```

這樣做的代價：
1. Kachu 需要自己維護 Temporal Server（複雜的基礎設施）
2. Approval 邏輯在 Kachu 內部重複建輪子（AgentOS 已有）
3. 三個產品（ForgeBase / ContentFlow / Kachu）各自有獨立的執行基礎設施，無法共享可觀測性

**AgentOS 已經提供了 Temporal 的核心功能**：
- Task/Run/Step 狀態持久化
- Approval gate + decide_approval
- Idempotency key
- Retry / execution policy
- Evidence / 可追溯

---

## 三、Kachu v2 架構

```
老闆 (LINE)
    │ 發訊息 / 照片
    ▼
Kachu LINE Webhook  (FastAPI)
    │
    ▼
Kachu Intent Router  (分類意圖)
    │ 識別出「需要啟動工作流」
    ▼
Kachu Task Trigger  → POST /tasks → AgentOS
                                        │
                                        ▼
                               AgentOS 執行工作流
                                        │ 每個 Step 呼叫
                                        ▼
                               Kachu Adapter / Tool APIs
                               (RAG, LLM, LINE, Google, Meta, GA4)
                                        │
                                        ▼ approval step 時
                               AgentOS Approval Gate
                                        │ 通知老闆
                                        ▼
                               Kachu Approval Bridge
                                        │ 推播 LINE Flex Message
                                        ▼
                                    老闆確認
                                        │ LINE postback
                                        ▼
                               Kachu 呼叫 AgentOS decide_approval
                                        │
                                        ▼
                               AgentOS 繼續執行
                                        │ 發布
                                        ▼
                               外部平台（LINE API / Google / Meta）
```

### 分工一覽

| 責任 | AgentOS | Kachu |
|------|---------|-------|
| 任務執行狀態追蹤 | ✅ Task / Run / Step | — |
| 審批與 HITL | ✅ Approval + decide_approval | — |
| Idempotency | ✅ idempotency_key | — |
| Retry / Timeout | ✅ ExecutionPolicy | — |
| Evidence / 可追溯 | ✅ Evidence | — |
| 意圖理解 | — | ✅ IntentRouter |
| 知識庫 / RAG | — | ✅ KnowledgeService + Qdrant |
| 四層記憶 | — | ✅ MemoryService |
| LINE 介面 | — | ✅ LineAdapter |
| Approval 通知橋 | — | ✅ ApprovalBridge（LINE Flex → decide_approval） |
| Google Business | — | ✅ GoogleBusinessAdapter |
| Meta IG/FB | — | ✅ MetaAdapter |
| GA4 數據 | — | ✅ GA4Adapter |
| LLM 呼叫 | — | ✅ LiteLLM Gateway |
| 認知工作流 | — | ✅ LangGraph（作為 AgentOS step 的 activity）|

---

## 四、核心工作流定義

### Workflow 1：`kachu_photo_content`
**觸發點**：LINE 收到照片  
**Idempotency key**：`{tenant_id}:{line_message_id}`

| Step | Name | Side Effect | 說明 |
|------|------|-------------|------|
| 1 | `analyze-photo` | READONLY | Gemini Vision 分析照片內容與場景 |
| 2 | `retrieve-context` | READONLY | RAG 檢索品牌知識 + 偏好記憶 |
| 3 | `generate-drafts` | READONLY | 生成三版草稿（IG/FB、Google 商家、官網） |
| 4 | `confirm-publish` | REVERSIBLE_WRITE + approval_timeout=86400s | 推播 LINE Flex Message，等老闆選擇 |
| 5 | `publish-content` | IRREVERSIBLE_WRITE | 依老闆選擇發布到各平台 |

**核心體驗**：老闆傳一張照片，隔天（甚至隔幾小時）按個確認，貼文就發出去了。

---

### Workflow 2：`kachu_review_reply`
**觸發點**：Google Webhook（新評論）  
**Idempotency key**：`{tenant_id}:{review_id}`

| Step | Name | Side Effect | 說明 |
|------|------|-------------|------|
| 1 | `fetch-review` | READONLY | 讀取評論內容與評分 |
| 2 | `analyze-sentiment` | READONLY | 情緒分析 + 策略判斷（正面/負面/建議） |
| 3 | `retrieve-context` | READONLY | RAG 取相關品牌資訊 |
| 4 | `generate-reply` | READONLY | 生成回覆草稿 |
| 5 | `confirm-reply` | REVERSIBLE_WRITE + approval_timeout=21600s（6h） | 推播 LINE，等確認 |
| 6 | `post-reply` | IRREVERSIBLE_WRITE | Google Business Profile API 發布回覆 |

**核心體驗**：有新評論，老闆收到推播，草稿已準備好，按確認就發出去。負評在 6 小時內必須處理。

---

### Workflow 3：`kachu_google_post`
**觸發點**：排程（每週）或老闆主動觸發  
**Idempotency key**：`{tenant_id}:{trigger_date}:{trigger_source}`

| Step | Name | Side Effect | 說明 |
|------|------|-------------|------|
| 1 | `determine-post-type` | READONLY | 判斷動態類型（What's New / Offer / Event） |
| 2 | `retrieve-context` | READONLY | RAG 取當季素材 + 搜尋關鍵字庫 |
| 3 | `generate-post` | READONLY | 生成動態草稿（含關鍵字置入） |
| 4 | `confirm-post` | REVERSIBLE_WRITE + approval_timeout=172800s（48h） | 推播 LINE |
| 5 | `publish-post` | IRREVERSIBLE_WRITE | Google Business Profile API 發布 |

---

### Workflow 4：`kachu_ga4_report`
**觸發點**：每週一 08:00 排程  
**Idempotency key**：`{tenant_id}:{week_start_date}`

| Step | Name | Side Effect | 說明 |
|------|------|-------------|------|
| 1 | `fetch-ga4-data` | READONLY | GA4 Data API 拉取上週數據 |
| 2 | `generate-insights` | READONLY | LLM 翻譯成人話（不是冷冰冰的數字） |
| 3 | `generate-recommendations` | READONLY | 生成 2-3 個具體可執行的建議 |
| 4 | `deliver-report` | REVERSIBLE_WRITE（無 approval gate） | 推播 LINE 週報 |

**設計說明**：週報是資訊性的，不需要 approval gate。但建議附上快速行動按鈕（「要我幫你更新 Google 商家動態嗎？」），老闆按了就觸發 Workflow 3。

---

### Workflow 5：`kachu_line_faq`
**觸發點**：LINE 收到顧客訊息（非老闆）  
**Idempotency key**：`{tenant_id}:{customer_line_id}:{message_timestamp}`

| Step | Name | Side Effect | 說明 |
|------|------|-------------|------|
| 1 | `classify-message` | READONLY | 判斷是否為 FAQ 可回答的問題 |
| 2 | `retrieve-answer` | READONLY | RAG 從知識庫找答案 |
| 3 | `generate-response` | READONLY | 生成回覆（或判斷需要升級人工） |
| 4 | `send-or-escalate` | REVERSIBLE_WRITE | FAQ → 直接回；複雜/情緒 → 通知老闆 |

**Abstain 機制**：找不到足夠依據時，回覆「已收到，老闆稍後回覆您」並推播通知老闆，不亂回答。

---

### Workflow 6：`kachu_knowledge_update`
**觸發點**：老闆傳「知識更新」類訊息（如「我們雞腿飯改成 90 元了」）

| Step | Name | Side Effect | 說明 |
|------|------|-------------|------|
| 1 | `parse-update` | READONLY | 解析老闆說的更新內容 |
| 2 | `diff-knowledge` | READONLY | 找出與現有知識庫的差異 |
| 3 | `confirm-update` | REVERSIBLE_WRITE + approval_timeout=3600s | 列出即將修改的條目，請老闆確認 |
| 4 | `apply-update` | IRREVERSIBLE_WRITE | 更新知識庫（PostgreSQL + Qdrant）|

**設計原則**：所有知識庫修改必須經老闆確認，不允許 AI 自作主張修改。

---

## 五、技術選型

### 保留（從 Kachu v1 移植）
- **LangGraph**：認知工作流（analyze → retrieve → generate），作為 AgentOS step 的 activity
- **LiteLLM Gateway**：所有 LLM 呼叫統一出口
- **Qdrant + PostgreSQL**：知識庫 + RAG
- **四層記憶架構**（Raw / Structured / Preference / Episodic）
- **Intent Router**：意圖分類（決定要觸發哪個 workflow）
- **LINE Adapter**：LINE Messaging API 整合
- **Google Business Adapter**：評論、動態發布
- **GA4 Adapter**：數據拉取
- **Meta Adapter**：IG/FB 發布（Phase 2）

### 捨棄（由 AgentOS 取代）
| Kachu v1 元件 | 由 AgentOS 取代的功能 |
|--------------|---------------------|
| Temporal Server | 任務執行狀態、耐久等待 |
| ApprovalService（橋接 Temporal Signal） | AgentOS Approval + decide_approval |
| 自己的 idempotency 管理 | AgentOS idempotency_key |
| 自己的 retry 邏輯 | AgentOS ExecutionPolicy |

### 新增（v2 特有）
- **AgentOS Approval Bridge**：接收 AgentOS approval 事件 → 推播 LINE Flex Message；接收 LINE postback → 呼叫 AgentOS decide_approval
- **Kachu Workflow Definitions**：在 AgentOS 內定義 6 個 Kachu 工作流
- **Kachu Adapter for AgentOS**：AgentOS 呼叫 Kachu Tool APIs 的 adapter

### 完整技術棧

| 層級 | 技術 | 角色 |
|------|------|------|
| 執行平台 | **AgentOS** | Task / Run / Approval / Idempotency / Evidence |
| 認知工作流 | **LangGraph** | 多步推理（只做認知，不做耐久等待） |
| LLM Gateway | **LiteLLM** | 模型路由 / Fallback / 成本控制 |
| 向量搜尋 | **Qdrant** | Dense + Sparse Hybrid Search |
| 關係資料庫 | **PostgreSQL** | 多租戶隔離、知識條目、記憶、稽核 |
| 快取 | **Redis** | 限流 / 佇列 / 排程鎖 |
| 觀測 | **Langfuse** | Trace 鏈：AgentOS Run ID → LangGraph Thread ID → Langfuse Trace |
| 主介面 | **LINE Bot** | 老闆主要操作入口（推播 + 確認） |
| 次介面 | **Web Dashboard** | 監控 / 知識庫管理 / 稽核（老闆不需要日常使用）|

---

## 六、Approval Bridge 設計

這是 Kachu v2 與 AgentOS 整合的關鍵橋接點。

### 流程

```
AgentOS 到達 approval step
    │
    ▼ Webhook 通知
Kachu Approval Bridge（/webhooks/agentOS/approval）
    │ 讀取 approval 事件
    ▼
組裝 LINE Flex Message（草稿內容 + 三個按鈕：確認 / 修改 / 先不用）
    │
    ▼ LINE Messaging API
老闆的 LINE 收到 Flex Message
    │
    ▼ 老闆點按鈕（LINE postback）
Kachu LINE Webhook（/webhooks/line）
    │ 解析 postback data（含 agentOS_run_id + approval_id）
    ▼
POST AgentOS /approvals/{approval_id}/decision
    │ {decision: "approved" | "rejected", context: {edited_payload: {...}}}
    ▼
AgentOS 繼續執行
```

### LINE Flex Message 格式

```json
{
  "type": "bubble",
  "header": {"text": "📸 新貼文草稿準備好了"},
  "body": {
    "contents": [
      {"text": "[IG/FB 版]"},
      {"text": "今日新品上市！我們的麻辣鴨血..."},
      {"text": "[Google 商家版]"},
      {"text": "台北最道地的麻辣鴨血..."}
    ]
  },
  "footer": {
    "contents": [
      {"action": {"type": "postback", "data": "action=approve&run_id=xxx", "label": "✅ 確認發布"}},
      {"action": {"type": "postback", "data": "action=edit&run_id=xxx", "label": "✏️ 我要修改"}},
      {"action": {"type": "postback", "data": "action=reject&run_id=xxx", "label": "❌ 先不用"}}
    ]
  }
}
```

---

## 七、DAY 0 入門流程

老闆第一次使用 Kachu，透過 LINE 完成以下流程（不需要填任何表格）：

```
步驟 1（30 秒）：LINE 傳來歡迎訊息
    → 只問三個問題：店名、行業類型、地址

步驟 2（無上限）：老闆上傳現有文件
    → 接受 PDF / Excel / 圖片 / Word / 任何格式
    → AI 背景解析（LlamaParse + Gemini Vision）

步驟 3（自動）：AI 整理摘要
    → 推播 LINE：「我整理好了，你看看對不對」
    → 老闆確認（Workflow 6 approval gate）

步驟 4（5-10 分鐘）：AI 訪談
    → LINE 聊天進行，三個問題：
      1. 你跟別家最不一樣的地方是什麼？
      2. 現在最大的困擾是什麼？
      3. 今年最想做的一件事是什麼？
    → 每個回覆即時萃取知識條目存入知識庫

步驟 5：完成
    → LINE 推播：「好了，我已經了解你的生意了。接下來我會主動幫你做事，你只需要確認。」
    → 連結 Google 商家（OAuth 授權）
    → 可選：連結 GA4、IG/FB
```

---

## 八、資料模型

### 核心資料表（Kachu 領域層）

```sql
-- 商家（租戶）
tenants (id, name, industry_type, address, created_at)

-- 知識條目（RAG 基礎）
knowledge_entries (
    id, tenant_id, category,  -- core_value|pain_point|goal|product|style|contact
    content, source_type,     -- document|conversation|photo|review|platform_data
    source_id, qdrant_point_id, created_at, updated_at, status
)

-- 對話原文（Raw Memory）
conversations (id, tenant_id, role, content, conversation_type, timestamp)

-- 工作流執行紀錄（與 AgentOS run_id 關聯）
workflow_records (
    id, tenant_id, agentOS_run_id,
    workflow_type,  -- photo_content|review_reply|google_post|ga4_report|line_faq
    trigger_source, trigger_payload,
    status, created_at
)

-- 待確認任務（ApprovalBridge 使用）
pending_approvals (
    id, tenant_id, agentOS_run_id, approval_id,
    task_type, draft_content, line_message_id,
    status, expires_at, created_at, decided_at, decision
)

-- 偏好記憶（從老闆修改行為學習）
preference_records (
    id, tenant_id, preference_type,  -- tone|format|platform|schedule
    original_draft, edited_version, diff_analysis,
    created_at
)

-- 連接器帳號（OAuth）
connector_accounts (
    id, tenant_id, platform,  -- google_business|meta|ga4|line_official
    access_token, refresh_token, token_expiry, status, created_at
)
```

### AgentOS 側（執行層，在 AgentOS 的資料庫）

AgentOS 的標準資料表（已存在）：
- `tasks` — 工作任務
- `runs` — 執行實例
- `steps` — 步驟執行紀錄
- `approvals` — 審批狀態
- `evidence` — 執行證據

---

## 九、開發路線圖

### Phase 0：地基（2 週）✅ 2026-04-26 完成
**目標：LINE → AgentOS → LINE approval 這條主幹線跑通**

- [x] AgentOS 內定義 Kachu 工作流（`kachu_photo_content` / `kachu_review_reply` / `kachu_line_faq` 三個工作流完整 step 定義）
- [x] Kachu LINE Webhook（接收訊息 → Intent Router → 觸發 AgentOS task）
- [x] Kachu Approval Bridge（LINE postback → AgentOS decide_approval）
- [x] DAY 0 入門流程（LINE 訪談 + 知識庫建立）
- [x] Kachu Adapter 基礎框架（AgentOS 可以呼叫 Kachu Tool APIs，11 個 stub 端點）

**驗收標準**：老闆傳一張照片 → LINE 收到草稿推播 → 按確認 → 系統記錄（還不發布）

**已交付（2026-04-26）：**
- `AgentOS/src/agent_platform/kachu_workflows/` — 三個工作流 pipeline
- `AgentOS/src/agent_platform/adapters/kachu_adapter.py` — KachuAdapter
- `Kachu-v2/src/kachu/` — FastAPI app 骨架（config / models / persistence / agentOS_client / line / tools / intent_router / approval_bridge）
- `Kachu-v2/src/kachu/onboarding/flow.py` — DAY 0 狀態機（9 states，知識庫建立）
- `Kachu-v2/src/kachu/persistence/tables.py` — 新增 KnowledgeEntryTable / ConversationTable / OnboardingStateTable
- `Kachu-v2/tests/test_photo_content_e2e.py` — Phase 0 整合測試 9/9 ✅
- `Kachu-v2/tests/test_onboarding_flow.py` — DAY 0 測試 13/13 ✅（共 22 tests 全過）

---

### Phase 1：核心體驗（4 週）✅ 完成
**目標：三個最高頻率的工作流完整跑通**

- [x] **Workflow 1**：`kachu_photo_content` — Gemini Vision 分析照片 + LiteLLM 生成草稿；Google 商家動態實際發布（2026-04-26）
- [x] **Workflow 2**：`kachu_review_reply` — Google Business Profile API 抓評論 + LiteLLM 生成回覆 + 實際發布（2026-04-26）
- [x] **Workflow 5**：`kachu_line_faq` — 顧客 vs 老闆 userId 區分 + LiteLLM 分類/回答 + 升級通知老闆（2026-04-26）
- [x] LangGraph 認知工作流 — 由 AgentOS 多步驟工作流（analyze → retrieve → generate）架構實現，四層記憶取代 LangGraph 狀態管理（2026-04-26）
- [x] Qdrant 向量知識庫 — OpenAI `text-embedding-3-small` embeddings + in-process cosine search（Qdrant 可選接入）；知識條目自動向量化（2026-04-26）
- [x] 四層記憶架構 — Raw（對話）+ Structured（知識+向量）+ Preference（老闆編輯差異）+ Episodic（工作流結果）（2026-04-26）
- [x] 偏好學習 — 老闆點「✏️ 我要修改」→ EditSession 狀態機捕捉 IG/Google 修改版 → 差異分析儲存 → 下次生成自動注入 few-shot 範例（2026-04-26）
- [x] DAY 0 入門流程（LINE 訪談 + 知識庫建立）← 已完成（2026-04-26）

**已交付（2026-04-26 完整交付）：**
- `src/kachu/llm/` — Gemini Vision + LiteLLM 統一介面
- `src/kachu/google/` — Google Business Profile client（評論抓取 / 回覆 / 商家動態發布）
- `src/kachu/memory/` — 四層記憶架構：embedder（OpenAI text-embedding-3-small）+ vector_search（純 Python cosine）+ manager（MemoryManager）
- `src/kachu/tools/router.py` — 11 個 Tool API：semantic search + preference injection + episode recording
- `src/kachu/line/webhook.py` — 老闆 vs 顧客分流；EditSession 偏好捕捉對話流
- `src/kachu/approval_bridge.py` — EDIT 動作：建立 EditSession + 引導老闆輸入修改版
- `src/kachu/persistence/tables.py` — 新增 PreferenceMemoryTable + EpisodicMemoryTable + EditSessionTable
- `credentials/google-service-account.json` — GBP service account
- `.env` — Gemini / OpenAI API keys 填入
- 測試 44/44 ✅（新增 22 個記憶架構測試）

**驗收標準**：找一個真實微型創業者，完成 DAY 0 入門，體驗完整的照片發文和評論回覆流程
**驗收標準**：找一個真實微型創業者，完成 DAY 0 入門，體驗完整的照片發文和評論回覆流程

---

### Phase 2：完整功能（4 週）✅ 2026-04-26 完成
**目標：六個工作流全部完成，老闆可以透過對話觸發任何工作流**

- [x] **Workflow 3**：`kachu_google_post` — 老闆主動觸發 + 5 步驟（retrieve-context → generate-google-post → notify-approval → confirm-google-post → publish-google-post）（2026-04-26）
- [x] **Workflow 4**：`kachu_ga4_report` — 3 步驟（fetch-ga4-data → generate-ga4-insights → send-ga4-report）無 approval gate，含快速行動建議（2026-04-26）
- [x] **Workflow 6**：`kachu_knowledge_update` — 5 步驟（parse-knowledge-update → diff-knowledge → notify-approval → confirm-knowledge-update → apply-knowledge-update）（2026-04-26）
- [x] Intent Router 完整版 — 關鍵字快速路徑（6 個 intent）+ LLM 分類（JSON 輸出）+ 錯誤時自動 fallback 到關鍵字；`dispatch()` 字典分派所有 6 個 intent（2026-04-26）
- [x] Google Business OAuth 整合 — `/auth/google/connect` → `/auth/google/callback`；GBP + GA4 一次授權；token 存入 ConnectorAccountTable（2026-04-26）
- [x] GA4 OAuth 整合 — 與 GBP 共用同一 OAuth 流程，同一次同意可取得 `analytics.readonly` scope；`GA4Client.run_report()` 呼叫 GA4 Data API（2026-04-26）
- [x] 推播頻率控制 — `PushLogTable` 記錄每次推播；`can_push(max_per_day, quiet_hours_start, quiet_hours_end)` 在 `notify-approval` 及 `send-ga4-report` 前進行門控（2026-04-26）

**已交付（2026-04-26 完整交付）：**

AgentOS 側：
- `AgentOS/src/agent_platform/kachu_workflows/knowledge_update_pipeline.py` — Workflow 6 完整 pipeline + `build_kachu_knowledge_update_plan()`
- `AgentOS/src/agent_platform/kachu_workflows/google_post_pipeline.py` — Workflow 3 完整 pipeline + `build_kachu_google_post_plan()`
- `AgentOS/src/agent_platform/kachu_workflows/ga4_report_pipeline.py` — Workflow 4 完整 pipeline + `build_kachu_ga4_report_plan()`
- `AgentOS/src/agent_platform/kachu_workflows/__init__.py` — 匯出 6 個 workflow definition + plan builder

Kachu-v2 側：
- `src/kachu/models.py` — `Intent` 新增 `KNOWLEDGE_UPDATE / GOOGLE_POST / GA4_REPORT / REVIEW_REPLY / FAQ_QUERY`；9 個新 request model
- `src/kachu/persistence/tables.py` — 新增 `PushLogTable`（`kachu_push_logs`）
- `src/kachu/persistence/repository.py` — 新增 7 個方法：`save_connector_account` / `get_connector_account` / `supersede_knowledge_entry` / `search_knowledge_entries_by_keywords` / `record_push` / `count_pushes_today` / `can_push`
- `src/kachu/config.py` — 新增 `GA4_PROPERTY_ID` / `GA4_REDIRECT_URI`
- `src/kachu/google/ga4_client.py` — `GA4Client`：`run_report()` + `parse_report()`
- `src/kachu/auth/oauth.py` — Google OAuth 2.0 flow（connect / callback / status）；`app.state.settings` 注入
- `src/kachu/tools/router.py` — 8 個新 Phase 2 端點；`notify-approval` 加入推播頻率門控
- `src/kachu/intent_router.py` — 完整重寫：關鍵字快路徑 + `classify_text_llm()` + `dispatch()` 字典分派
- `src/kachu/line/webhook.py` — 老闆文字分支改用 `classify_text_llm()` + 分派所有 6 個 intent
- `src/kachu/main.py` — 新增 `oauth_router` + `_engine` 參數（測試共享引擎）；**新增 `google_webhook_router`（`POST /webhooks/google/review`）+ APScheduler lifespan（Workflow 3/4 排程觸發）**
- `src/kachu/google/webhook.py` — Google Pub/Sub 推播接收；解碼 base64 review_id → 觸發 `kachu_review_reply` AgentOS 任務
- `src/kachu/scheduler.py` — `KachuScheduler`：`ga4_weekly_report`（每週一 08:00）+ `google_weekly_post`（每週四 10:00）
- `src/kachu/persistence/repository.py` — 新增 `list_active_tenant_ids()`（排程 / webhook 多租戶迭代）
- `pyproject.toml` — 新增 `apscheduler>=3.10.0` 依賴
- `tests/test_phase2_workflows.py` — 39 個 Phase 2 測試（全部通過）

**測試結果：83/83 ✅**（Phase 0/1：44 + Phase 2：39）

**驗收標準**：老闆說「幫我寫個七夕活動動態」→ 系統完整執行 → 老闆確認發布

---

### Phase 2.5：AgentOS 能力完整落地（3 週，已完成）
**目標：消除所有 stub / skipped / recorded-only 路徑，讓 Kachu v2 完整運用 AgentOS 的執行保證能力**

> Phase 2 完成的是「有這個功能」，Phase 2.5 要完成的是「這個功能真的可靠、可觀測、可追責」。  
> 沒有 Phase 2.5，AgentOS 只是一個可選的執行框架；有了它，AgentOS 才是 Kachu 真正的信任地基。

**驗收紅線（全部達到才算完成）：**
- 老闆「修改草稿」全流程無錯誤，AgentOS `edited_payload` 正確傳遞到發布步驟
- IG/FB 發布路徑沒有任何 `recorded` / `not yet implemented` 回應
- `cancel` / `retry` / `replay` 三個生命週期操作 AgentOS API 暴露且 Kachu 可觸發
- 六個 workflow idempotency key 全部有效（包含 GA4 report）
- Day 0 文件/圖片上傳不再只是 placeholder，會被實際解析並寫入知識庫
- 所有 approval / run 事件有 Langfuse trace，可在儀表板查詢
- 端到端整合測試覆蓋「正常通過」「老闆拒絕」「超時到期」三種 approval 結果

**目前狀態（2026-04-27）：**
- Phase 2.5 七個 WP 已完成實作並完成回歸驗證
- AgentOS 補齊 `cancel` / `retry` / `replay` API、`replayed_from_run_id` schema/migration、approval lifecycle 與 idempotency 測試
- Kachu 補齊 Day 0 文件解析、Meta 實際發布、Google/GA4 pipeline 對齊、ApprovalBridge EDIT 流程測試、Langfuse LLM trace 關聯
- 最新驗證結果：AgentOS `24 passed`、Kachu-v2 `94 passed`，合計 `118 passed`
- Alembic 已驗證可升級至 `head`（含 `0003_run_replay_link`）

---

#### WP-1：ApprovalBridge EDIT 路徑修復與 `edited_payload` 回傳

**問題根源**：`_start_edit_session` 函式尾端含有重複貼入的死路程式碼，`agentOS_decision` 變數在此 scope 未定義，會導致老闆點「✏️ 我要修改」後整個流程靜默失敗。

**待辦清單：**
- [ ] 移除 `approval_bridge.py` `_start_edit_session` 方法尾端約 30 行重複/無效程式碼
- [ ] 補上完整的 EDIT 完成路徑：EditSession 收到 IG 修改版 → 收到 Google 修改版 → 組裝 `edited_payload` → 呼叫 `decide_approval(approval_id, {decision: "approved", edited_payload: {...}})`
- [ ] AgentOS KachuAdapter `confirm-publish` 步驟：確認 `edited_payload` 真的從 `request.context["approvals"]` 被讀取並傳遞給 `publish-content` 步驟的 `selected_platforms` 與文案
- [ ] 補測試：`test_approval_bridge_edit_complete_flow` — 走完 EditSession 全程（create → waiting_ig → waiting_google → complete → decide_approval with edited_payload）
- [ ] 補測試：`test_kachu_adapter_confirm_publish_uses_edited_payload` — 驗證修改後的文案與平台選擇確實覆蓋原草稿

**驗收標準**：老闆點「我要修改」→ LINE 問 IG 版 → 老闆輸入 → LINE 問 Google 版 → 老闆輸入 → AgentOS 以 `edited_payload` 繼續 → 發布用修改版 ✅

---

#### WP-2：IG/FB 與跨平台實際發布（去除 `recorded-only`）

**問題根源**：`publish-content` 對非 google 平台一律回傳 `{"status": "recorded", "note": "Phase 1: not yet implemented"}`。

**待辦清單：**
- [ ] Meta Graph API 整合：`src/kachu/meta/` 模組，實作 `post_to_ig_fb(access_token, image_url, caption)`
- [ ] `connector_accounts` 表新增 `meta` 平台欄位；`/auth/meta/connect` + `/auth/meta/callback` OAuth 流程
- [ ] `publish-content` tool：若 `selected_platforms` 包含 `ig_fb` 且有 Meta connector → 呼叫 Meta API，記錄 `published`；若無 credentials → 回傳 `skipped_no_credentials`（而非 `recorded`，語意要準確）
- [ ] 錯誤分離：Meta 發布失敗不應影響 Google 發布；各平台結果獨立回傳
- [ ] 補整合測試：mock Meta API，驗證成功路徑 + 401 失敗路徑的 side_effects 記錄

**驗收標準**：有 Meta connector 的租戶，照片貼文後 IG/FB 帳號真的出現貼文 ✅  
無 Meta connector 的租戶，狀態為 `skipped_no_credentials` 而非 `recorded` ✅

---

#### WP-3：AgentOS API 契約對齊與 Cancel / Retry / Replay 暴露

**問題根源一**：規劃文件描述 Kachu 收到 `approval.created` webhook 再推播 LINE，但目前實作是 AgentOS 執行 step 中同步呼叫 `notify-approval` tool 推播 LINE。這兩種方式的結果相同但架構語意不同；需要選定一種並統一文件與程式碼。

**問題根源二**：AgentOS `main.py` 缺少 `POST /tasks/{task_id}/cancel`、`POST /runs/{run_id}/retry`、`POST /runs/{run_id}/replay` 等操作 API，導致 Kachu 對「卡住的 run」「工具失敗的 run」「需要重新跑一次的 run」沒有一致控制面。

**名詞統一（避免後續文件與程式碼再分裂）：**
- `cancel`：停止尚未完成的 task/run，進入 terminal state，不再繼續執行
- `retry`：只針對 `FAILED` run，從失敗步驟重新執行，已成功 step 依 idempotency / replay 狀態略過
- `replay`：以原始輸入建立一個新的 run，從 Step 1 重跑，保留舊 run 作為歷史紀錄
- 本 Phase **不使用** `resume` 一詞，避免和 `retry` / `replay` 混淆

**待辦清單：**
- [ ] **AgentOS** 新增 `POST /tasks/{task_id}/cancel` API（已有 `TaskStatus.CANCELED`，缺 endpoint）
- [ ] **AgentOS** 新增 `POST /runs/{run_id}/retry`：對 `FAILED + failure_mode == TOOL_CALL_FAILED` 的 run 重新執行，跳過已成功的步驟（`ToolCallStatus.SKIPPED_REPLAY`）
- [ ] **AgentOS** 新增 `POST /runs/{run_id}/replay`：以同一份 input/context 建立新 run，從 Step 1 重跑，並在 evidence 中保留 `replayed_from_run_id`
- [ ] **Kachu** `AgentOSClient` 加入 `cancel_task(task_id)`、`retry_run(run_id)`、`replay_run(run_id)` 方法
- [ ] **Kachu** 新增 LINE postback action `cancel_run`、`retry_run`、`replay_run`，在 LINE Flex 的失敗通知中帶上對應按鈕
- [ ] **文件**：在「與 AgentOS 的合約介面」表格中更正 `POST /runs/{run_id}/approve` → `POST /approvals/{approval_id}/decision`，並移除 `approval.created` webhook 描述（改為說明目前採用 step-driven push 模式）
- [ ] 補 AgentOS 測試：`test_cancel_running_task`、`test_retry_failed_run_skips_succeeded_steps`、`test_replay_run_creates_new_run_with_same_input`

**驗收標準**：老闆拒絕草稿後，Kachu 可向 AgentOS 查詢失敗 run，重新觸發同一任務 ✅  
AgentOS `/runs` API 有可查的 `CANCELED` / `FAILED` / `REPLAYED_FROM` 關聯 run 列表 ✅

---

#### WP-4：Workflow 規格與 Pipeline 實作對齊

**缺口清單（產品計畫 vs 當前程式碼）：**

| Workflow | 計畫規格 | 當前缺口 |
|----------|----------|----------|
| `kachu_google_post` | Step 1 應為 `determine-post-type`（判斷 STANDARD / EVENT / OFFER） | 目前 Step 1 直接是 `retrieve-context`，post type 靠 `topic` 欄位硬傳 |
| `kachu_ga4_report` | Step 3 應為 `generate-recommendations`（2-3 個快速行動建議），idempotency key 為 `{tenant_id}:{week_start_date}` | 目前 Step 3 是 `send-ga4-report`，`idempotency_key_builder = None` |
| `kachu_line_faq` | Abstain 機制：找不到答案要回「已收到，老闆稍後回覆您」再通知老闆 | `retrieve-answer` 回傳 `should_escalate=True` 時，`send-or-escalate` 有通知老闆路徑，但沒有自動回覆顧客「已收到」的確認訊息 |
| `kachu_knowledge_update` | idempotency key 包含 `{trigger_date}` | 目前以週哈希為 key，重新觸發同週同訊息會被阻擋，正常；但需補文件說明設計意圖 |

**待辦清單：**
- [ ] **`kachu_google_post`**：在 AgentOS pipeline 插入 `determine-post-type` 作為 Step 1（tool: `POST /tools/determine-post-type`）；在 Kachu tool router 新增 `/tools/determine-post-type` endpoint（LLM 判斷 + fallback STANDARD）；KachuAdapter 新增對應 step 分支
- [ ] **`kachu_ga4_report`**：將 `generate-ga4-insights` 拆為 `generate-ga4-insights`（純摘要）+ `generate-recommendations`（行動建議）兩個 step，並在 `send-ga4-report` step 組合輸出；補 `idempotency_key_builder`：key 為 `ga4_report:{tenant_id}:{week_start_date}`（周一 ISO date）
- [ ] **`kachu_line_faq`** Abstain 路徑：在 `send-or-escalate` 中，`should_escalate=True` 時除通知老闆外，若顧客 `customer_line_id` 可觸及，先自動回覆顧客「已收到您的訊息，老闆稍後親自回覆您 😊」
- [ ] 更新對應測試，確認新 step 的 plan 結構與 adapter 呼叫

**驗收標準**：`build_kachu_google_post_plan` 返回 6 steps（含 `determine-post-type`）✅  
GA4 weekly report 排程觸發不會在同一週重複執行 ✅  
顧客 FAQ 問到不會回答的問題時，顧客收到「已收到」確認訊息 ✅

---

#### WP-5：Day 0 文件解析上線與知識庫真正落地

**問題根源**：目前 onboarding 雖然有 9-state flow，但文件/圖片/語音上傳仍是 placeholder，沒有真的經過 LlamaParse / Gemini Vision 解析，也沒有被整理成可檢索知識。這會讓 Kachu 在最關鍵的 Day 0 建庫階段失真。

**待辦清單：**
- [ ] `src/kachu/onboarding/flow.py`：把文件/圖片/語音 upload 分支從 placeholder 改為真正 parser pipeline
- [ ] 文件類型分流：PDF / DOCX 走 LlamaParse；圖片走 Gemini Vision；語音先走 Whisper 類 ASR 再摘要
- [ ] `KnowledgeService`：將解析後內容正規化為 `KnowledgeEntry`，保留 `source_uri`、`source_type`、`parsed_at`、`confidence`
- [ ] 為 onboarding 補 `document_parse_failed` / `document_parse_succeeded` side effect 與老闆回饋訊息
- [ ] 若 parser 失敗，回覆老闆「已收到，但這份檔案需要人工整理」而不是假裝已進知識庫
- [ ] 補測試：`test_onboarding_document_upload_parses_into_knowledge_entries`、`test_onboarding_parse_failure_falls_back_to_manual_review`

**驗收標準**：老闆在 Day 0 上傳菜單、價目表、品牌介紹圖卡後，知識庫新增可檢索條目且可被 `retrieve-context` 命中 ✅

---

#### WP-6：Langfuse Trace 串接與 AgentOS 可觀測性

**問題根源**：AgentOS `tracing.py` 有 `record()` 介面，但目前是同程序記憶體實作（in-process trace list），無法在 Langfuse 查詢。Kachu 的 LLM 呼叫也沒有跟 AgentOS run_id 關聯。

**待辦清單：**
- [ ] **AgentOS** `tracing.py`：加入 Langfuse SDK（`langfuse.Langfuse()`）作為可選後端；當環境變數 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 存在時，`record()` 同時發送到 Langfuse（以 `run_id` 作為 trace ID，`event_type` 作為 span name）
- [ ] **Kachu** `src/kachu/llm/`：所有 `generate_text()` 呼叫加入 Langfuse observation，使用 `run_id` 關聯到 AgentOS trace
- [ ] **Kachu** `config.py` 新增 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST`（預設 `https://cloud.langfuse.com`）
- [ ] 新增 `/admin/traces/{run_id}` dashboard endpoint，直接從 Langfuse API 拉取並展示 trace 鏈
- [ ] 補 smoke test：起一個 mock Langfuse server，驗證 trace event 確實被發出

**驗收標準**：AgentOS 執行一個 Kachu workflow 後，Langfuse 儀表板可看到該 run 的完整 step trace ✅  
每個 LLM 呼叫有 `run_id` 關聯，可從 Kachu LLM span 追溯到 AgentOS 的哪個 step ✅

---

#### WP-7：端到端整合測試補齊與 Workflow DoD 檢查清單

**問題根源**：83 個現有測試大多是 stub 路徑測試（無 LLM key 時的 fallback），無法驗證真實執行路徑；也沒有覆蓋 approval 生命週期中的失敗情境。

**待辦清單：**
- [ ] `tests/test_approval_lifecycle.py`：對 AgentOS service 直接測試三條路徑：
  - `APPROVE` → run 完成，證據鏈完整
  - `REJECT` → run 狀態 `FAILED`，`failure_mode == APPROVAL_REJECTED`
  - Timeout 超時 → run 狀態 `FAILED`，`failure_mode == APPROVAL_TIMEOUT`（手動推進時間）
- [ ] `tests/test_idempotency.py`：同一 idempotency key 觸發兩次 → 第二次回傳相同 task_id，不重複執行
- [ ] `tests/test_edit_session_to_publish.py`：EditSession 完整流程 → `edited_payload` 到達 `publish-content` step
- [ ] `tests/test_publish_content_with_meta.py`：mock Meta API → `ig_fb` platform 真實呼叫 + side_effect `ig_fb_published`
- [ ] `tests/test_onboarding_document_ingestion.py`：真實 parser adapter mock + KnowledgeEntry 寫入驗證
- [ ] 在 CI / 測試 README 中補充「哪些測試需要真實 API key」與「哪些是純 mock 可直接跑」的分類說明
- [ ] 在產品計畫中補一張六條 workflow 的 DoD 表，定義每條 pipeline 的「必須成功」「可接受降級」「失敗即不算完成」
- [ ] 更新 `Kachu-v2-Product-Plan.md` 的 Phase 2 測試計數，反映 Phase 2.5 新增項目

**Phase 2.5 完成後的實際測試計數（2026-04-27）：** AgentOS `24` + Kachu-v2 `94` = **118 tests**

**六條 workflow 的 DoD（Definition of Done）**

| Workflow | 必須成功 | 可接受降級 | 失敗即不算完成 |
|----------|----------|------------|----------------|
| `kachu_photo_content` | 草稿生成、approval 決策、至少一個已授權平台成功發布、evidence 可回查 | 未授權平台標記 `skipped_no_credentials` | EDIT 流程壞掉、發布只回 `recorded`、approval 結果無 evidence |
| `kachu_review_reply` | 抓到 review、生成草稿、老闆決策後完成回覆或明確拒絕 | 無 GBP credentials 時可標記 `skipped_no_credentials` 並通知老闆需補連線 | 回覆成功與否不明、老闆拒絕後仍繼續發送 |
| `kachu_line_faq` | FAQ 命中時正確回覆顧客；未命中時升人工並通知老闆 | LLM 失敗可 fallback 模板回覆 | 未命中卻沉默、沒有回覆顧客「已收到」 |
| `kachu_knowledge_update` | parse/diff/approval/apply 全鏈完成，舊知識 superseded 正確 | 解析信心不足可要求人工確認再寫入 | 未經 approval 就直接覆寫知識 |
| `kachu_google_post` | post type 判斷、草稿生成、approval、GBP 發布完成 | 無 GBP connector 可先生成草稿但不得宣稱已發布 | post type 缺失、發布結果不可追蹤 |
| `kachu_ga4_report` | 同週只跑一次、數據摘要 + recommendations 生成並送達老闆 | 無 GA4 connector 可標記 `skipped_no_credentials` 並提醒補連線 | 同週重複發送、沒有建議只發原始數字 |

**驗收標準**：
- `pytest tests/ -m "not requires_api_key"` 全部通過 ✅
- Phase 2.5 七個 WP 的驗收紅線全數達成 ✅
- `approval_bridge.py` `_start_edit_session` 無重複死路程式碼 ✅
- AgentOS `main.py` 暴露 `/tasks/{id}/cancel`、`/runs/{id}/retry`、`/runs/{id}/replay` ✅
- `publish-google-post` 和 `publish-content` (ig_fb) 在有 credentials 時真實發出 API 呼叫 ✅
- `kachu_ga4_report` pipeline 有 idempotency key ✅
- Day 0 文件上傳會真的進 parser 並落地知識庫 ✅
- Langfuse trace 在 production 環境可查 ✅

---

**Phase 2.5 工作包優先序（建議執行順序）**

| 順序 | WP | 原因 |
|------|----|------|
| 1 | WP-1 ApprovalBridge EDIT 修復 | 直接影響老闆體驗的最高頻路徑，目前有 Bug，修完就消除最大信任風險 |
| 2 | WP-5 Day 0 文件解析上線 | 建庫品質是後面所有 RAG / 草稿品質的上游，越晚補成本越高 |
| 3 | WP-3 Cancel / Retry / Replay / API 對齊 | AgentOS 可靠性的基礎；Kachu 對「壞了可以修」的能力依賴這裡 |
| 4 | WP-4 Workflow 規格對齊 | 產品行為與文件要一致，客戶試用時才不會困惑 |
| 5 | WP-2 IG/FB 實際發布 | 直接商業價值，但需要 Meta App 審核時間，早點啟動 |
| 6 | WP-7 端到端測試補齊 | 貫穿所有 WP，每完成一個 WP 就同步補測試 |
| 7 | WP-6 Langfuse 串接 | 可觀測性，影響長期信心與 Phase 3 評測 |

---

### Phase 3：品質與學習（4 週）
**目標：把 Draft Acceptance Rate 推過 70%；記憶系統從「有寫入」升級到「有影響決策」**

> Phase 2.5 之後，Kachu 有六個可靠的 workflow，也有四層記憶架構。  
> Phase 3 的任務是：讓記憶系統真正影響草稿品質，而不只是存在資料庫裡。

#### 評測基礎建設

- [ ] Golden Set 評測集：餐飲 / 美業 / 顧問三類各 20 筆「輸入 → 理想草稿」的基準題庫
- [ ] Draft Acceptance Rate 追蹤（老闆第一次就接受的比例，目標 > 70%）
- [ ] Escalation Accuracy 追蹤（FAQ 應升人工的有沒有正確升；不該升的有沒有亂升）
- [ ] A/B Prompt Testing 框架：同一 tenant 的同類任務，可以比較兩版 prompt 策略的接受率差異
- [ ] `kachu_eval` CLI：給定 golden set，跑完整 tool pipeline，輸出 acceptance rate / precision / recall 報告

#### 記憶主動注入（讓記憶真的影響輸出）

- [ ] `retrieve-context` step 除回傳知識條目外，附加排序後的 `preference_memory`（最近 10 筆老闆編輯 diff），注入 `generate-drafts` 的 few-shot 段落
- [ ] `generate-drafts` prompt 架構升級：`[品牌知識] + [老闆偏好範例] + [當週 GA4 洞見（若有）] → 草稿`；三個來源明確分區，可個別追蹤命中率
- [ ] Episodic memory 回饋迴路：每次 `APPROVED` / `REJECTED` / `EDITED` 都寫入 `episode` 條目，並在下次同類任務前由 `retrieve-context` 讀取最近 5 筆 episode 作為 outcome hint
- [ ] Preference calibration 報告：每兩週推播 LINE 給老闆，展示「你最常修改的地方是 X，我已學會 Y 筆偏好」，並提供「重置偏好」選項

#### 跨租戶學習（匿名化）

- [ ] 同行業類型的「被接受草稿」pattern 可匿名彙整成 `shared_style_hints`，供同類型但知識庫稀疏的新租戶使用
- [ ] 提供 opt-in 機制；預設 opt-out，老闆主動同意才共享

**驗收標準**：Golden Set acceptance rate ≥ 70% ✅  
`retrieve-context` response 包含 `preference_hints` 且可在 Langfuse trace 中驗證被注入 ✅  
每次 approval 決策都產生 `episode` 知識條目 ✅

---

### Phase 4：Adaptive Planning — 記憶驅動個人化（6 週）
**目標：讓 AgentOS 的執行策略與 Kachu 的記憶系統真正耦合；老闆用得越久，agent 越有自主判斷能力**

> Phase 2.5 打通了 runtime 骨架；Phase 3 讓記憶影響了草稿品質。  
> Phase 4 更進一步：記憶直接影響「要不要等老闆確認」「要做幾個步驟」「用哪種工具」這些執行決策。  
> 這是 AgentOS Approval Policy + ExecutionPolicy 真正被 Kachu 業務邏輯驅動的階段。

#### 4-A：動態 Approval Policy（基於租戶行為歷史）

目前所有 `publish_external` step 一律需要人工審批（high risk / irreversible）。  
引入歷史行為後，approval 可以更細緻：

- [ ] `TenantApprovalProfile` 模型：記錄每個租戶的 `recent_acceptance_rate`（最近 30 天）、`median_edit_delta`（老闆平均修改幅度）、`avg_approval_latency`（老闆平均多久回覆）
- [ ] `KachuExecutionPolicyResolver`：在 `build_*_plan()` 時讀取 `TenantApprovalProfile`，動態設定每個 step 的 `approval_required` 與 `approval_timeout_seconds`
  - `recent_acceptance_rate > 0.85` + `median_edit_delta < 0.1` → `confirm-publish` timeout 從 86400s 降至 21600s（老闆信任度高，短通知即可）
  - `recent_acceptance_rate < 0.5` → 在 `generate-drafts` 前插入一個輕量「確認主題方向」step，減少無效草稿
- [ ] 政策調整須透明：每次動態調整都寫入 AgentOS trace 並在 evidence 中標示 `policy_adapted_from_profile`
- [ ] 老闆可透過 LINE 指令「恢復預設確認模式」手動覆蓋

#### 4-B：Outcome-Based Plan Adaptation（根據歷史結果改寫 Plan）

- [ ] `EpisodicMemoryAnalyzer`：分析最近 20 筆同類型 episode（e.g. `workflow_type = kachu_photo_content`），產生 `PlanAdaptationHint`
  - 若連續 3 次 IG 草稿被拒，下次 plan 自動在 `generate-drafts` 前加入 `check-recent-rejections` step 做 pre-flight 修正
  - 若最近 GA4 report 顯示某時段流量高，`generate-google-post` 的 topic hint 自動帶入時段資訊
- [ ] `build_*_plan()` 接受 `adaptation_hints` 參數，允許動態插入或跳過 step（不修改 AgentOS 核心）
- [ ] Adaptation 歷史可追查：每次適應都記錄在 AgentOS evidence（`evidence_type = plan_adapted`）

#### 4-C：動態工具選擇（超越固定 pipeline 順序）

目前每個 workflow 的 step 順序是靜態定義的。Phase 4 開始探索讓 Kachu 在部分 step 自行選擇工具：

- [ ] `retrieve-context` step 改為「策略性 RAG」：先判斷本次任務類型，再決定要從知識庫、偏好記憶、episodic memory 中各取幾筆，而不是固定取全部
- [ ] `generate-drafts` step 可選接入「近期競品觀察」（若老闆設定了觀察名單）或跳過，由 agent 基於置信度決定是否補充外部脈絡
- [ ] 每個選擇決策都以 `tool_call` 記錄在 AgentOS，trace 中可見「為什麼選這個工具」的 reasoning

#### 4-D：AgentOS 能力擴充需求（Phase 4 依賴）

| 需要的新能力 | 建議實作位置 | 說明 |
|-------------|------------|------|
| Plan builder 接受動態 `adaptation_hints` | Kachu workflow registry | 不動 AgentOS core，只在 `build_*_plan()` 層注入 |
| Execution policy 可由 plan builder 傳入 | AgentOS `service.py` `run_task()` | 允許 task payload 攜帶 per-step policy override |
| Evidence type `plan_adapted` | AgentOS models（generic） | 僅加 enum value，符合 product-agnostic 原則 |
| `GET /tenants/{tenant_id}/approval-profile` | Kachu API | 純 Kachu 領域，不進 AgentOS core |

**驗收標準**：同一租戶使用 30 天後，`TenantApprovalProfile.recent_acceptance_rate` 計算正確 ✅  
`build_kachu_photo_content_plan()` 在高信任租戶下產出 approval_timeout 縮短的 plan ✅  
Adaptation 被記錄在 AgentOS evidence 且 Langfuse 可追查 ✅

---

### Phase 5：Proactive Multi-Run Agent — 主動式長週期代理（持續演進）
**目標：Agent 不只是「老闆說才做」；開始主動發現機會、提出計劃、執行跨工作流的長週期代理行為**

> Phase 0-4 都是「反應式」agent：老闆傳訊息 → 老闆有評論 → 排程到了 → agent 才做。  
> Phase 5 要達到的是：agent 自己觀察狀態、發現問題、提出方案、等老闆輕量確認後執行。  
> 這才是 AgentOS 最高層代理能力（goal-driven planning）的完整展現。

#### 5-A：跨工作流情境共享（Cross-Workflow Context）

目前每個工作流的知識是獨立的，不會互相影響。Phase 5 開始建立跨 workflow 的情境流動：

- [ ] `GA4 report` → `Google Post` 連動：GA4 report 產出的 `generate-recommendations` 結果自動寫入 `SharedContext` 表，`build_kachu_google_post_plan()` 在下一次排程時讀取作為 topic hint
- [ ] `Review Reply` → `FAQ Knowledge` 連動：老闆在回覆評論時輸入的答案，自動提取為候選知識條目並推播 LINE 詢問「要加入 FAQ 知識庫嗎？」
- [ ] `Photo Content` → `Preference Memory` 連動：發布成功後，若該貼文在 Meta/Google 獲得良好互動（靠 webhook 回饋），自動記錄為高品質 episodic memory 案例

#### 5-B：主動問題發現（Proactive Issue Detection）

- [ ] `ProactiveMonitorAgent`：每天定時（e.g. 07:00）掃描各租戶狀態，發現以下任一條件即主動觸發通知任務：
  - 近 7 天未發布任何內容 → 推播「本週還沒有發文，需要我幫你準備一篇嗎？」
  - 近 14 天有未回覆的負面評論（rating ≤ 2） → 推播警示 + 快速行動按鈕
  - 知識庫超過 60 天未更新 → 推播「你的知識庫已有一段時間沒更新，要確認一下價格或菜單有沒有變？」
  - GA4 流量本週下跌 > 20% → 推播摘要 + 建議觸發 Google Post
- [ ] 以上通知都走 AgentOS task（不直接推 LINE）：確保可追蹤、可取消、有 approval gate 讓老闆選擇「現在處理 / 之後再說 / 不用管」

#### 5-C：長週期計畫（Long-Horizon Planning）

- [ ] `ContentCalendarAgent`：每月初讓 agent 提出一份 4 週的內容排程草案
  - 依據：季節、近期評論主題、GA4 流量趨勢、老闆過去的發文偏好
  - 輸出：每週一張 LINE Flex Message，列出「建議主題 + 建議 post type + 建議時間」
  - 老闆可一次確認整月計畫，後續每週排程自動執行，只在執行前 24 小時再提醒一次
- [ ] 計畫以 AgentOS multi-step task 表達：每個月份計畫是一個 `task`，每週執行是一個 `run`，保持完整可追蹤性

#### 5-D：Goal-Driven Intent 解析（超越固定 intent 分類）

目前 `IntentRouter` 有固定的 6 個 intent 類型，老闆的每句話必須被分類到其中一個。  
Phase 5 開始探索更高層的目標解析：

- [ ] `GoalParser`：當老闆說的話不符合任何現有 intent（e.g. 「最近生意不太好，你覺得怎麼辦？」），不再回「不理解」，改為：
  1. LLM 分析老闆的問題屬於哪個領域（流量、知名度、評論、內容、價格）
  2. 從可用 workflow 中提出最相關的「建議行動清單」
  3. 老闆從清單中選一個 → 觸發對應 workflow
- [ ] 這個路徑不自動執行，只提案；選擇權永遠在老闆手上

#### 5-E：AgentOS 能力擴充需求（Phase 5 依賴）

| 需要的新能力 | 建議實作位置 | 說明 |
|-------------|------------|------|
| Agent-initiated task creation（非 webhook 觸發）| Kachu `ProactiveMonitorAgent` → `POST /tasks` | AgentOS 已支援，Kachu 端補定時觸發邏輯 |
| Multi-run task（一個 task 跨多個排程 run）| AgentOS `service.py` + `run_task` | 目前一個 task 對應一個 run；需要支援「預定義的 run 序列」語意 |
| Cross-task shared context | Kachu `SharedContext` 表（Kachu 領域） | 不動 AgentOS core；以 evidence 做跨 run 資訊橋接 |
| `evidence_type = cross_workflow_hint` | AgentOS models（generic） | 僅加 enum value |

**驗收標準**：`ProactiveMonitorAgent` 在 staging 環境每日掃描並正確識別「14 天無發文」的租戶 ✅  
`ContentCalendarAgent` 產出可被老闆一次確認的月計畫 LINE Flex Message ✅  
跨 workflow context 流動：GA4 report 的建議出現在下週 Google Post 的 topic hint 中 ✅  
`GoalParser` 在無法分類時正確提出行動清單而非回傳錯誤 ✅

---

### Phase 6：產品收斂與可營運化（4 週）
**目標：讓 Kachu v2 從「功能很多的 agent 系統」收斂成「老闆真的可以放心每天使用的 AI 營運幕僚服務」**

> 到 Phase 5 為止，Kachu v2 的方向是對的，但風險已不在於功能不夠，而在於系統變大後，容易讓老闆體驗、產品邊界與上線品質一起變得不穩。  
> Phase 6 的任務不是再加更多能力，而是把 Kachu 收斂成一個穩定、可預期、可持續擴張的產品。

#### 6-A：核心體驗穩定化

- [x] 建立單一 release 驗證入口：`make release-check` 或等效 script，至少串起 Kachu 測試、AgentOS 測試、production-safe smoke test
- [x] 將 smoke test 固化為「臨時 tenant 建立 → 驗證核心流程 → 自動清理」的標準腳本，避免每次 deploy 靠手動拼指令
- [x] 部署流程明確區分 `build`、`migrate`、`up -d`、`smoke` 四階段；任一階段失敗即不得進入下一步
- [x] 在產品與操作文件中明訂「healthy 不等於可用」：container health 只是前置條件，真正要驗證的是老闆會碰到的 workflow 體驗

#### 6-B：關鍵流程一致性補強

- [x] 為每個 Kachu workflow 建立 contract 測試矩陣：`request model`、`adapter step`、`workflow_input propagation`、`scheduler payload`、`approval callback`
- [x] 所有新增 step 必須至少有一個「plan builder test + adapter test」成對存在，避免規格有了、實際路徑卻沒接通
- [x] 為 `retrieve-context`、`generate-drafts`、`check-draft-direction`、`notify-approval` 這類高頻邊界建立固定 schema 驗證
- [x] 對 scheduler 觸發路徑與互動式觸發路徑做 parity test，確保同一產品能力不會因入口不同而行為分裂

#### 6-C：產品邊界收斂

- [x] 明文化 Kachu / AgentOS 邊界：Kachu 負責產品邏輯、租戶策略、外部平台整合；AgentOS 負責 runtime、approval、retry、trace、idempotency
- [x] 新需求若可在 adapter、workflow registry、composition layer 解決，禁止直接下沉到 AgentOS core runtime
- [x] 為跨系統欄位建立變更規範：新增欄位時必檢查 `models -> adapter -> tool router -> tests` 四個位置
- [x] 將高風險 workflow 的 payload 欄位整理成對照表，減少未來 phase 再發生「有加欄位但沒傳到底」

#### 6-D：營運可見性與除錯能力

- [x] 定義 workflow run 關鍵事件鏈：`task_created`、`plan_built`、`adapter_called`、`approval_pending`、`approval_decided`、`publish_attempted`、`publish_succeeded|failed`
- [x] 將上述事件對齊到 Langfuse / AgentOS trace / Kachu log，至少能以 `tenant_id`、`task_id`、`run_id` 串起完整軌跡
- [x] 補上 production debug playbook：常見故障從哪一層看起、用哪個 id 對 trace、先查哪個 service
- [x] 對主動推播、外部發佈、approval decision 加上可查詢的審計欄位，讓營運問題能被快速定位，而不是只能翻 raw log

#### 6-E：功能節奏與產品收斂原則

- [x] Phase 6 期間新工作以「穩定老闆高頻體驗 > 補邊界缺口 > 補可觀測性 > 補自動化 > 新能力」排序
- [x] 暫緩新增全新 workflow 類型，除非能直接帶來商業驗證或重大營收影響
- [x] 暫緩把更多 product-specific 行為塞進 AgentOS core，避免 Kachu 被 runtime 視角反過來主導
- [x] 每完成一個治理項，回寫到 `README` 或操作文件，避免產品知識只存在對話與臨時指令中

#### 6-F：優先順序（建議執行順序）

| 順序 | 項目 | 產品意義 |
|------|------|----------|
| 1 | Release gate + smoke script 固化 | 先讓每次上線都可預期，不要讓老闆體驗暴露在 deploy 風險裡 |
| 2 | Contract test 矩陣補齊 | 先穩住跨邊界流程，避免功能看似存在、實際無法可靠運作 |
| 3 | 邊界與 payload 規範 | 先把 Kachu 當產品、AgentOS 當平台的責任切清楚 |
| 4 | Trace / audit / debug playbook | 先讓 production 問題能快速定位，減少營運中斷時間 |
| 5 | 新功能恢復節奏 | 只有前四項穩住後，擴功能才不會再次把產品拖散 |

**驗收標準**：每次部署都能以單一指令跑完測試 + smoke gate，且 smoke 會自動清理測試資料 ✅  
老闆高頻使用的主流程，在不同入口下都呈現一致行為，不再出現「規劃有、實際沒接通」的 release blocker ✅  
任一 production 問題可在 15 分鐘內以 `run_id` 或 `task_id` 定位到失敗層級（Kachu / AgentOS / external adapter）✅  
Phase 6 結束前，Kachu 的產品敘事仍然是「微型創業者的 AI 營運幕僚」，而不是被 AgentOS runtime 敘事反客為主 ✅

---

### AgentOS 能力利用率全覽

| AgentOS 能力 | Phase 0-2.5 現狀 | Phase 3-4 目標 | Phase 5 目標 |
|-------------|-----------------|---------------|-------------|
| Task / Run / Step 執行 | ✅ 完整使用 | ✅ 持續 | ✅ 多 run 序列 |
| Approval + HITL | ✅ 完整使用 | 動態 timeout + policy | 輕量確認模式 |
| Idempotency | ✅ 完整使用 | ✅ 持續 | ✅ 持續 |
| Cancel / Retry / Replay | ✅ 完整使用 | ✅ 持續 | ✅ 持續 |
| Evidence / 可追溯 | ✅ 使用（基本） | `plan_adapted` evidence | `cross_workflow_hint` |
| Trace / Langfuse | ✅ 完整串接 | 細分至 prompt section | cross-run trace chain |
| Execution Policy（動態） | ❌ 靜態設定 | **Phase 4 首次啟用** | 持續強化 |
| Plan 動態生成 | ❌ 固定 pipeline | **Phase 4 首次啟用** | goal-driven |
| Agent-initiated task | ❌ 僅被動 webhook / 排程 | — | **Phase 5 首次啟用** |
| Cross-run context 共享 | ❌ 每 run 獨立 | — | **Phase 5 首次啟用** |
| Goal-driven intent | ❌ 固定 6 intent | — | **Phase 5 首次啟用** |

---

## 十、不做的事（邊界）

| 不做 | 理由 |
|------|------|
| 自維護 Temporal | AgentOS 已提供相同能力 |
| 全自動發布（不經老闆確認）| 微型創業者需要信任建立，不能跳過 HITL |
| 無限 LINE 推播 | 老闆會覺得被打擾，一天最多 3 則 |
| 承諾 100% AI 客服 | FAQ 以外的訊息一定要升人工 |
| 同時開發三個產品 | 先做完 Kachu，帶著經驗做 ContentFlow 和 ForgeBase v2 |

---

## 十一、與 AgentOS 的合約介面

Kachu 依賴 AgentOS 的以下 API：

| AgentOS API | Kachu 使用時機 |
|-------------|--------------|
| `POST /tasks` | Intent Router 識別出需要啟動工作流 |
| `POST /tasks/{task_id}/run` | 啟動 task 執行，取得 run_id |
| `POST /approvals/{approval_id}/decision` | 老闆在 LINE 按確認/拒絕/修改後，Kachu 以 approval_id 送出決定（approval_id 透過 `GET /runs/{run_id}` 輪詢取得）|
| `GET /runs/{run_id}` | 查詢工作流執行狀態 + 取得 pending approval_id |
| `GET /runs/{run_id}/evidence` | 讀取工作流執行結果（發布後的狀態）|
| `POST /tasks/{task_id}/cancel` | 老闆取消任務（Phase 2.5 WP-3 補齊）|
| `POST /runs/{run_id}/retry` | 失敗後重試，跳過已成功 step（Phase 2.5 WP-3 補齊）|
| `POST /runs/{run_id}/replay` | 用原始輸入建立新 run，從 Step 1 重跑（Phase 2.5 WP-3 補齊）|

> **注意**：AgentOS 目前採用 step-driven push 模式 — AgentOS 在 `notify-approval` step 直接呼叫 Kachu `/tools/notify-approval` endpoint，由 Kachu tool 向老闆推播 LINE Flex Message。**無** `approval.created` / `run.completed` webhook（原計畫描述有誤，已更正）。

AgentOS 呼叫 Kachu Tool APIs：

| Kachu Tool API | AgentOS adapter 使用時機 |
|---------------|------------------------|
| `POST /tools/analyze-photo` | `analyze-photo` step |
| `POST /tools/retrieve-context` | `retrieve-context` step |
| `POST /tools/generate-drafts` | `generate-drafts` step |
| `POST /tools/publish-content` | `publish-content` step |
| `GET /tools/fetch-review/{review_id}` | `fetch-review` step |
| `POST /tools/post-reply` | `post-reply` step |
| 其他工作流 steps 類推... | |

---

## 十二、第一個里程碑

**在開始任何程式碼之前，先驗證一件事：**

> 找一個真實的美甲師或餐飲老闆，用現在能運作的任何工具（甚至只是 LINE 手動回覆），模擬她的一天：
> 1. 早上開店時，LINE 推播昨天的評論，草稿已準備好
> 2. 中午傳一張新品照片，LINE 推播三版貼文草稿
> 3. 晚上按確認，發布

**這個模擬告訴你：哪個環節她會卡住，哪個她覺得有用。**

那個卡住的地方，就是 Phase 0 最值得做的東西。
