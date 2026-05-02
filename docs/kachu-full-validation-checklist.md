# Kachu v2 完整驗證清單

> 版本：2026-05-01  
> 原則：本清單全部打勾 = Kachu 所有核心能力均已在真實環境驗證，可對外承諾。  
> 範圍：基礎建設 → 平台串接 → 6 個 Workflow → Approval 鏈路 → 排程 → 邊界行為

> 補充原則：外部平台若仍處於開發端審批、配額核准或商務准入階段，只能列為內部驗證項目；在審批完成前，不應納入正式 onboarding 或對外宣稱為可用功能。

---

## 說明：Kachu 的完整鏈路

老闆所有操作的底層都走同一條路：

```
老闆 LINE 訊息 / 系統排程
  → Kachu LINE Webhook / Scheduler
    → Intent Router（意圖分類）
      → AgentOS Task（建立並執行 workflow）
        → Kachu Tool APIs（LLM / RAG / Platform）
          → AgentOS Approval Gate（需確認時）
            → ApprovalBridge → LINE Flex Message 給老闆
              → 老闆按確認 → LINE postback
                → ApprovalBridge → AgentOS decide_approval
                  → workflow 繼續
                    → 外部平台（Meta / Google / GA4）
```

每個 workflow 都必須走完這條路，才算真實驗證。

---

## 一、基礎建設驗證

### 1-A. 服務健康

- [x] `GET https://app.kachu.tw/health` 回傳 200（2026-05-02 確認回傳 `{"status":"ok","service":"kachu"}`）
- [x] PostgreSQL 連線正常（container `kachu-v2-postgres-1` healthy，`psql` 可查詢）
- [x] Redis 連線正常（container `kachu-v2-redis-1` healthy，`PING → PONG`）
- [x] AgentOS 連線正常（`kachu-v2-agentos-1` running，`POST /tasks` 成功建立 Task）
- [x] Qdrant：**未部署**（prod 使用 in-process cosine similarity，直接查 PostgreSQL `embedding` 欄位；Qdrant 為選配架構，尚未接入）

### 1-B. 環境設定

- [x] 正式主機：`root@172.234.85.159`，`/opt/kachu-v2`
- [x] `KACHU_BASE_URL=https://app.kachu.tw`
- [x] `.env.prod` 為當前部署版本（非 `.env`）
- [x] `LINE_BOSS_USER_ID=U1f7215a15f956a462bd196b19cc30f87`
- [x] `META_APP_ID=1361751429123912`（Opsly Business，企業商家）
- [x] `GOOGLE_OAUTH_CLIENT_ID` 已設定（Google OAuth 流程可通代表已設定）
- [x] `GOOGLE_OAUTH_CLIENT_SECRET` 已設定
- [x] `GOOGLE_AI_API_KEY` 已設定（`gemini/gemini-2.5-flash` 文字生成 + Vision + `gemini-embedding-2` Embedding，dim=1536）

### 1-C. LLM 連線

- [x] LLM 呼叫正常（`POST /tools/generate-drafts` 200 OK，Gemini 2.5 Flash 回傳草稿，2026-05-02 確認）
- [x] Intent Router LLM 分類可正常回傳（老闆傳照片 → `intent=photo_content` 正確分類）
- [x] 生成貼文草稿時 LLM 有回傳內容（非空、無幻覺；`brand_brief` 污染問題已修復，2026-05-02）

### 1-D. AgentOS 整合

- [x] `POST /tasks`（AgentOS）可成功建立 Task，回傳 `task_id`（2026-05-02 確認）
- [x] Task 可進入 Running 狀態（photo_content workflow 正常執行到 waiting_approval）
- [x] Approval 事件可觸發 Kachu 的 `/tools/notify-approval`（路徑已修正：AgentOS 呼叫此端點，Kachu 推 Flex 給老闆；2026-05-02 WF1 驗證確認）
- [x] `POST /approvals/{id}/decision` 可成功呼叫（`AgentOSClient.get_pending_approval_id_for_run()` + `decide_approval()` 在 production 容器內直接測試，回傳正確 approval_id，2026-05-02）

---

## 二、Onboarding 驗證（DAY 0 入門流程）

> 前置條件：老闆第一次加入 LINE Bot 時需完成。`step != completed` 時所有 LINE 功能均被攔截。
> 驗證原則：不得以手動修改 PostgreSQL 取代 LINE 問答；手動 DB 操作只可用於回滾或清理測試污染，不算流程驗證通過。

### 2-A. 流程完整性

- [x] 新 tenant 傳第一則訊息 → Kachu 回歡迎訊息 + 問店名
- [x] 回答店名 → 問行業
- [x] 回答行業 → 問地址
- [x] 回答地址 → 進入 awaiting_docs（資料上傳）
- [x] 傳「跳過」→ 進入 interview_q1（awaiting_docs 期間上傳了 4 張產品圖，Gemini 自動解析成 document KB 條目）
- [x] 回答 q1（核心價值）→ 進入 q2
- [x] 回答 q2（最大困擾）→ 進入 q3
- [x] 回答 q3（今年目標）→ Kachu 回「🎉 太好了！...傳一張照片給我」
- [x] DB 確認：`kachu_onboarding_states.step = completed`

### 2-B. Redo 機制（2026-05-01 修復）

- [x] interview_q2 中說「重新回答第一題」→ 正確倒回 q1，不存入 DB
- [x] interview_q3 中說「重新回答第二題」→ 正確倒回 q2，不存入 DB
- [x] 倒回後重新作答 → 最終 step = completed，KB 內容正確

### 2-C. 現況

- [x] onboarding redo bug 已修復並部署（2026-05-01）
- [x] 老闆 LINE 帳號（`U1f7215a15f956a462bd196b19cc30f87`）完成三題問答，`step = completed`（2026-05-02 19:54，店名：坐骨新經 陳老師，行業：保健食品，地址：新北市泰山區仁義路222號）
- [x] 2026-05-01 已將 boss tenant 從錯誤的手動補資料狀態回滾為 `step = new`，並清空 onboarding 產生的 `basic_info/core_value/pain_point/goal/document`

---

## 三、LINE 觸發鏈驗證

### 3-A. Webhook 基礎

- [x] LINE Webhook URL：`https://app.kachu.tw/webhooks/line`（active = true）
- [x] 老闆傳訊息 → Kachu 收到 `POST /webhooks/line 200 OK`
- [ ] Signature 驗證正常（偽造 signature 會被拒絕）
- [ ] 非老闆訊息（`line_user_id != LINE_BOSS_USER_ID`）走 FAQ 路徑，不走 boss 路徑

### 3-B. 意圖分類（Intent Router）

- [x] 老闆傳照片 → intent = `photo_content`（2026-05-02 確認，多次觸發均正確分類）
- [x] 老闆傳「幫我寫一篇動態」→ intent = `google_post`（2026-05-03 LINE 截圖驗證：回傳 Google 商家動態草稿卡）
- [ ] 老闆傳「我們現在營業時間改了」→ intent = `knowledge_update`
- [ ] 老闆傳「這週流量怎樣」→ intent = `ga4_report`
- [ ] 老闆傳「回覆這則評論」→ intent = `review_reply`
- [x] 老闆傳一般聊天 → intent = `general_chat`（2026-05-03 production 重測通過：「你好」與「早安，今天辛苦了」皆回一般寒暄，不再誤走品牌/流量策略 consult 路徑）

### 3-C. Postback 處理（老闆按 LINE Flex 按鈕）

- [x] 按「🚀 立即發布」→ `ApprovalBridge.handle_postback(action=APPROVE)`（2026-05-02 WF1 完整鏈路確認）
- [x] 按「🗓️ 排程發布」→ 進入 LINE 排程對話、要求老闆輸入月/日/時並二次確認（2026-05-03 production 驗證通過）
- [ ] 按「❌ 先不用」→ `ApprovalBridge.handle_postback(action=REJECT)`
- [ ] 按「✏️ 我要修改」→ 進入 edit session，老闆可修改文案
- [ ] 修改後送出 → 以修改後內容呼叫 `AgentOS decide_approval(approved)`

---

## 四、Workflow 1：photo_content（主要驗證項目）

> 觸發：老闆 LINE 傳照片  
> 完整鏈路：LINE 照片 → AgentOS Task → LLM 分析+生成草稿 → LINE Flex 確認 → Meta/Google 發布

### 4-A. 前置條件

- [x] Meta connector 已存入 tenant `U1f7215a15f956a462bd196b19cc30f87`（2026-05-02 已完成 Meta OAuth；`fb_page_id=940149472511909`，`fb_page_name=四時循養堂（原坐骨新經）`，`ig_user_id` 仍為空）
- [x] onboarding `step = completed`（2026-05-02 確認，店名：坐骨新經 陳老師，行業：保健食品）

### 4-B. 照片接收與分析

- [x] 老闆傳照片 → Kachu 下載圖片 bytes 成功（2026-05-02）
- [x] AgentOS Task `kachu_photo_content` 建立成功（2026-05-02，`kachu_workflow_runs` 有記錄）
- [x] `analyze-photo` step 完成（Gemini Vision 分析照片，200 OK）
- [x] `retrieve-context` step 完成（RAG 從 KB 取品牌知識，200 OK）
- [x] `generate-drafts` step 完成（LLM 生成 IG/FB 草稿，200 OK）

### 4-C. Approval 鏈路

- [x] `confirm-publish` step 觸發 ApprovalBridge（`notify-approval` 200 OK）
- [x] 老闆 LINE 收到 Flex Message（含草稿文案 + 四個按鈕：立即發布 / 排程發布 / 我要修改 / 先不用）
- [x] Flex Message 內容與 LLM 草稿一致（非空、非範本填充失敗；已確認無幻覺，店名/地址/品項正確）
- [x] 老闆按『🚀 立即發布』→ Kachu 呼叫 `AgentOS decide_approval(approved)`（2026-05-02 完整鏈路驗證通過）

### 4-D. 一次性 LINE 排程發布

- [x] 老闆按「🗓️ 排程發布」→ Kachu 在 LINE 詢問預計發布時間（2026-05-03 production 驗證）
- [x] 老闆回覆月/日/時，若未填分鐘則預設整點；Kachu 會先覆述解析後時間再要求確認（2026-05-03 production 驗證）
- [x] 老闆按「確認排程」→ Kachu 建立持久化 scheduled publish 記錄，不依賴記憶體 job（2026-05-03 production 驗證）
- [x] 到點後 `LINE Scheduled Publish Dispatch` 會準時執行並成功發布 Facebook 貼文（2026-05-03 production 驗證）
- [x] 排程發布會一併帶出圖片；2026-05-03 已修正缺圖 root cause，並回填 production 舊的 12 筆 pending photo approvals

### 4-E. 發布到 Meta

- [x] `publish-content` step 執行 `MetaClient.post_fb_photo()`（2026-05-02 確認）
- [x] Facebook Page（`940149472511909`）實際出現該貼文（2026-05-02 確認，四時循養堂（原坐骨新經））
- [x] 貼文內容與老闆確認的草稿一致（2026-05-02 確認）
- [x] 老闆 LINE 收到「✅ 已發布到 Facebook」確認訊息（2026-05-02 確認）
- [ ] 已截圖保存 Facebook 貼文畫面

### 4-F. 發布到 Instagram（需 ig_user_id）

- [ ] Meta connector 中有 `ig_user_id`（需客戶帳號，其 Page 已連結 IG 商業帳號）
- [ ] `MetaClient.post_ig_photo()` 成功（兩步驟：container → publish）
- [ ] Instagram 帳號實際出現該圖文
- [ ] 已截圖保存 Instagram 發文畫面

### 4-G. 成功判準

- [ ] 照片傳出 → LINE Flex 收到：整體 < 30 秒
- [ ] 老闆確認 → 貼文上線：整體 < 60 秒
- [ ] Audit log 可追蹤完整 Task Run ID

---

## 五、Workflow 2：review_reply（Google 評論回覆）

> 觸發：Google Webhook 收到新評論，或老闆 LINE 主動觸發  
> 完整鏈路：評論 webhook → AgentOS → 分析+草稿 → LINE 確認 → GBP 發布

### 5-A. 前置條件

- [ ] Google connector 已存入指定 tenant（含 `access_token`、`account_id`、`location_id`）
- [ ] 有一則可安全測試的真實評論（建議自己留一則）

### 5-B. 評論接收

- [ ] Google Review Webhook 觸發 Kachu（`POST /webhooks/google/review`）
- [ ] Kachu 讀取評論內容與評分成功

### 5-C. Approval 鏈路

- [ ] `generate-reply` step 完成（LLM 生成回覆草稿）
- [ ] 老闆 LINE 收到 Flex Message（含評論原文 + 草稿回覆）
- [ ] approval_timeout = 6 小時，逾時未回覆行為符合預期（log 紀錄，不靜默失敗）
- [ ] 老闆按確認 → `post-reply` step 呼叫 GBP API 發布回覆

### 5-D. 驗證結果

- [ ] Google Business 後台可看到該評論回覆
- [ ] 已截圖保存結果

---

## 六、Workflow 3：google_post（GBP 最新動態）

> 觸發：每週排程 或 老闆 LINE 說「幫我寫一篇動態」

### 6-A. 老闆主動觸發路徑

- [ ] 老闆 LINE 傳「幫我寫一篇動態」→ intent = `google_post`
- [ ] AgentOS Task `kachu_google_post` 建立
- [ ] `generate-post` step 完成（含 SEO 關鍵字）
- [ ] 老闆 LINE 收到 Flex Message（含草稿）
- [ ] 老闆確認 → GBP API 發布
- [ ] Google Business 前台可看到新動態
- [ ] 已截圖保存

### 6-B. 排程觸發路徑

- [ ] Scheduler 每週觸發 `kachu_google_post` Task
- [ ] 無老闆介入也能自動推播草稿到 LINE（approval gate = 48 小時）
- [ ] 老闆未在時限內回應 → 系統行為符合預期（記錄逾時，不發布）

---

## 七、Workflow 4：ga4_report（GA4 週報）

> 觸發：每週一 08:00 排程（或老闆 LINE 觸發）

### 7-A. GA4 連線

- [ ] GA4 connector 已存入 tenant（含 `ga4_property_id`）
- [ ] `GA4Client.run_report()` 可成功拉取數據

### 7-B. 報告生成與推播

- [ ] 老闆 LINE 傳「這週流量怎樣」→ intent = `ga4_report`
- [ ] `fetch-ga4-data` step 完成
- [ ] `generate-insights` step 完成（LLM 翻成人話）
- [ ] `deliver-report` step 推播 LINE 週報（**無需 approval gate**）
- [ ] 老闆 LINE 收到週報內容（含數字 + 文字解讀）
- [ ] 週報附有快速行動按鈕（「要幫你發 Google 動態嗎？」）

### 7-C. 排程路徑

- [ ] Scheduler 每週一 08:00 自動執行，無需老闆觸發

---

## 八、Workflow 5：line_faq（LINE 顧客 FAQ）

> 觸發：非老闆的 LINE 用戶傳訊息  
> 注意：顧客訊息不應走老闆路徑

### 8-A. 路徑分離

- [ ] 非 `LINE_BOSS_USER_ID` 傳訊息 → 走 FAQ 路徑（非 boss 路徑）
- [ ] FAQ 路徑：`kachu_line_faq` Task 建立

### 8-B. RAG 回答

- [ ] `retrieve-answer` step：RAG 從 KB 找答案
- [ ] `generate-response` step：LLM 生成回覆
- [ ] 可回答 → 直接回覆顧客 LINE
- [ ] 知識庫無法回答（Abstain 機制）→ 回「已收到，老闆稍後回覆」+ 推播通知老闆
- [ ] 老闆收到通知訊息（含顧客原文）

---

## 九、Workflow 6：knowledge_update（知識庫更新）

> 觸發：老闆 LINE 說「我們雞腿飯改成 90 元了」

### 9-A. 解析與確認

- [ ] 老闆傳更新訊息 → intent = `knowledge_update`
- [ ] `parse-update` step 完成（解析要更新的內容）
- [ ] `diff-knowledge` step 完成（與現有 KB 比較差異）
- [ ] 老闆 LINE 收到 Flex Message（列出即將修改的條目，請確認）
- [ ] approval_timeout = 1 小時

### 9-B. 更新執行

- [ ] 老闆確認 → `apply-update` step 更新 PostgreSQL + Qdrant
- [ ] DB 確認：`kachu_knowledge_entries` 條目已更新
- [ ] 更新後 RAG 查詢可取到新內容

---

## 十、Proactive Monitor（主動監控）

> 排程定期執行，主動推播 nudge 給老闆

### 10-A. Nudge 類型驗證

- [ ] **NUDGE_NO_POST**：超過 N 天未發布任何貼文 → 老闆收到提醒訊息
- [ ] **NUDGE_NEGATIVE_REVIEW**：有未回覆的負評 → 老闆收到提醒 + 評論內容
- [ ] **NUDGE_STALE_KNOWLEDGE**：KB 超過 N 天未更新 → 老闆收到提醒

### 10-B. 排程執行

- [ ] `scheduler.py` 定期觸發 `ProactiveMonitorAgent.run()`
- [ ] nudge 推播到老闆 LINE 成功
- [ ] LINE API 失敗（timeout）時有 log，不 crash

---

## 十一、平台 OAuth 完整驗證

### 11-A. Meta OAuth

- [x] 開啟 `/auth/meta/connect?tenant_id=<tenant>`
- [x] 使用目標 Facebook 使用者完成授權（2026-05-02，boss tenant）
- [x] callback 顯示「Meta 已連結成功」成功頁
- [x] connector 寫入 `meta` platform，`fb_page_id=940149472511909`，`fb_page_name=四時循養堂（原坐骨新經）`，`last_refreshed_at=2026-05-02 02:22:04`
- [x] 確認目前綁定的粉絲專頁是否為目標粉專（production DB 已寫入 `fb_page_name=四時循養堂（原坐骨新經）`）
- [ ] `ig_user_id` 寫入成功（需客戶帳號，其 Page 已連結 IG 商業帳號）
- [ ] token 過期後 refresh 機制正常

### 11-B. Google OAuth

- [x] 開啟 `/auth/google/connect?tenant_id=<tenant>&platforms=gbp`
- [x] 使用目標 Google 帳號完成授權（2026-05-01，smoke tenant `oauth-smoke-20260501`，屬內部驗證）
- [x] callback 回傳 `{"status":"connected","tenant_id":"oauth-smoke-20260501","platforms":["google_business"]}`（屬內部驗證）
- [x] connector 寫入 `google_business` platform（`last_refreshed_at`: 2026-05-01T01:41:04）
- [x] connector 中有 `access_token`、`refresh_token`
- [x] connector 中有 `account_id`、`location_id`（backfill 成功，2026-05-02 確認：賃笙堂 陳老師 (target) 已寫入；屬內部驗證）
- [ ] GBP API 配額 / 准入審批完成（Case `3-9905000040433`，7-10 工作天）
- [ ] Google 功能已從「內部驗證」切換為「正式可對外開放」
- [ ] 正式使用者 onboarding / LINE 流程開放 Google 串接入口
- [ ] 老闆 tenant（`U1f7215a15f956a462bd196b19cc30f87`）在正式開放後完成 Google OAuth

### 11-C. Google OAuth（GA4）

- [ ] GA4 connector 包含 `ga4_property_id`
- [ ] `GA4Client` 可成功呼叫 GA4 Data API

---

## 十二、邊界行為與容錯驗證

### 12-A. 重複訊息（Idempotency）

- [ ] 同一張照片（同一 `line_message_id`）傳兩次 → 只建立一個 AgentOS Task
- [ ] 重複觸發 `kachu_google_post`（同一天）→ idempotency key 防重複

### 12-B. Approval 逾時

- [ ] photo_content approval 超過 24 小時未回應 → 系統正確記錄逾時，不靜默失敗
- [ ] review_reply approval 超過 6 小時未回應 → 系統正確紀錄

### 12-C. 平台 API 失敗

- [ ] Meta API 返回錯誤 → Kachu log 記錄，老闆 LINE 收到失敗通知
- [ ] Google API 返回錯誤 → 同上
- [ ] LLM 呼叫失敗 → fallback 行為符合預期（回覆老闆「稍後重試」）

### 12-D. Instagram 無圖限制

- [ ] 試圖對 Instagram 發純文字貼文 → 系統正確 skip IG，只發 FB
- [ ] log 有 `Instagram does not support text-only posts` 記錄

---

## 十三、Dashboard 驗證

- [ ] `https://app.kachu.tw/dashboard` 可存取
- [ ] 顯示 Task/Run 列表（AgentOS 資料）
- [ ] 顯示 Knowledge Entries 列表
- [ ] 顯示 Audit Events

---

## 十四、Meta Insights 與留言管理驗證

> 觸發：老闆 LINE 查詢成效數據、或系統自動撈取貼文留言並通知老闆回覆  
> 前置條件：Meta OAuth 已授權，且 token 包含 `read_insights`、`pages_manage_engagement`、`instagram_manage_comments` 三個新 scope（2026-05-02 已授權）

### 14-A. FB Page Insights（read_insights）

- [ ] `POST /tools/fb-page-insights` 可成功呼叫（Meta token 有 `read_insights` scope）
- [ ] 回傳 `page_impressions`、`page_engaged_users`、`page_post_engagements` 等指標
- [ ] 指定 `period=week` 與 `period=month` 均可正常拉取
- [ ] token 無 `read_insights` 時回傳明確錯誤訊息（非靜默失敗）

### 14-B. FB Post Insights（read_insights）

- [ ] `POST /tools/fb-post-insights` 可成功呼叫（帶入真實 `post_id`）
- [ ] 回傳 `post_impressions`、`post_engagements`、`post_reactions_by_type_total` 等指標
- [ ] `post_id` 不存在時回傳明確錯誤訊息

### 14-C. FB 留言管理（pages_manage_engagement）

- [ ] `POST /tools/fb-list-comments` 可列出 FB 貼文的留言列表
- [ ] `POST /tools/fb-reply-comment` 可成功回覆 FB 留言（Facebook 頁面可見回覆）
- [ ] `POST /tools/fb-hide-comment` 可成功隱藏 FB 留言（`is_hidden=true`）
- [ ] `POST /tools/fb-hide-comment` 可成功取消隱藏（`is_hidden=false`）
- [ ] token 無 `pages_manage_engagement` 時回傳明確錯誤（非靜默失敗）

### 14-D. IG 留言管理（instagram_manage_comments）

- [ ] `POST /tools/ig-list-comments` 可列出 IG 媒體的留言列表（需 `ig_user_id`）
- [ ] `POST /tools/ig-reply-comment` 可成功回覆 IG 留言（Instagram 可見回覆）
- [ ] `POST /tools/ig-hide-comment` 可成功隱藏 IG 留言（`hide=true`）
- [ ] token 無 `instagram_manage_comments` 時回傳明確錯誤

### 14-E. 成功判準

- [ ] 所有 Insights API 呼叫 < 5 秒回傳
- [ ] 所有留言操作 < 3 秒完成
- [ ] Audit log 記錄每次操作（操作人 = system 或 boss）

---

## 十五、驗證結果總覽

### 基礎建設

- [ ] 服務健康：全部 healthy
- [ ] LLM 連線：可用
- [ ] AgentOS 整合：可用

### Onboarding

- [x] 完整流程走通（含 redo 機制）

### LINE 觸發鏈

- [ ] Webhook 收訊：正常
- [ ] Intent Router：已驗證 `photo_content` / `google_post` / `general_chat`，其餘待測
- [ ] Postback：approve / reject / edit 全部正常

### Workflow 驗證

- [x] WF1 photo_content：FB 真實發文成功（2026-05-02 完整 approval-first 鏈路全段驗證通過，FB 頁面可見貼文，含照片）
- [x] WF1 photo_content：LINE 一次性排程發布成功（2026-05-03 production 驗證通過，準時發布且含圖片）
- [ ] WF1 photo_content：IG 真實發文成功（需 ig_user_id）
- [ ] WF2 review_reply：GBP 評論回覆成功（需 GBP quota）
- [ ] WF3 google_post：GBP 動態發布成功（需 GBP quota）
- [ ] WF4 ga4_report：週報推播成功
- [ ] WF5 line_faq：顧客 FAQ 回覆 + abstain 機制正常
- [ ] WF6 knowledge_update：KB 更新 + RAG 確認

### 排程 / Proactive

- [ ] Proactive Monitor：3 種 nudge 均可觸發
- [ ] Scheduler：定時任務正確觸發

### 平台 OAuth

- [x] Meta OAuth：Facebook Page 已授權且 page name 已確認；新 3 個 scope 已授權（2026-05-02）；待補齊 `ig_user_id`
- [x] Google OAuth：**內部流程本身已驗證**（smoke tenant，connector 已存入 DB）
- [ ] Google OAuth：GBP 審批完成後，才可對正式使用者開放
- [ ] Google OAuth（boss tenant）：待正式開放後再進行授權
- [ ] GA4 OAuth：待驗證

### Meta Insights & Comment Management

- [ ] FB Page Insights：`/tools/fb-page-insights` 可呼叫，指標正確回傳
- [ ] FB Post Insights：`/tools/fb-post-insights` 可呼叫，貼文指標正確
- [ ] FB 留言管理：list / reply / hide 三個端點均可用
- [ ] IG 留言管理：list / reply / hide 三個端點均可用（需 ig_user_id）

---

## 目前阻塞項目

| 阻塞項目 | 原因 | 預計解除 |
|----------|------|----------|
| ~~WF1 FB 最後一哩驗證~~ | ✅ 已完成（2026-05-02）：photo_content approval-first 完整鏈路驗證通過，FB 頁面有圖文貼文 | — |
| 老闆三句手動驗證後續追查 | 2026-05-02 production root cause 已釐清：第二句本輪確實走 consultation，尚未形成 clarify blocker；第三句 `knowledge_update` 其實已在 AgentOS `waiting_approval`，但因 Kachu 原本未明確帶 `line_message_id` 作為 idempotency key，重送同句時會重連舊 pending run，導致本地 DB 看不到新的 approval / push / audit。另，歷史 `document` 污染主要來自舊資料，已再收窄顯式知識吸收 cue 以降低再次誤吸風險；相關修補已部署 production，待重新驗證 | 待重新驗證 |
| Google 對外開放（boss tenant / 正式 onboarding）| GBP API 配額審批中（Case `3-9905000040433`）；目前只完成內部 OAuth smoke 驗證，不應先暴露給正式使用者 | 7-10 工作天 |
| Instagram 發文（WF1-E）| Meta connector 目前缺 `ig_user_id`；需確認粉專已連結 IG 商業帳號並重新授權 | 依客戶進度 |
| ~~14 筆 KB embeddings 為 NULL~~ | ✅ 已修復（2026-05-02）：production 容器內執行 re-embed 腳本，14/14 補齊 1536 dims，`SELECT COUNT(*) WHERE embedding IS NULL = 0` | — |
| ~~onboarding 完成~~ | ✅ 已完成（2026-05-02）| — |

---

> 更新紀錄：
> - 2026-05-01：初版建立；onboarding redo bug 已修復
> - 2026-05-01（更新）：Google OAuth 流程驗證成功（smoke tenant `oauth-smoke-20260501`），多次 504 為 Kachu rebuild 期間打來所致，非 code bug
> - 2026-05-01（更新）：boss tenant 曾因測試污染與手動補資料偏離正規流程；現已回滾為 `step = new`，後續需從 LINE 正式重跑 onboarding
> - 2026-05-02：boss tenant 從 LINE 正式完成完整 onboarding 流程（店名：坐骨新經 陳老師，行業：保健食品，地址：新北市泰山區仁義路222號）；awaiting_docs 期間上傳 4 張產品圖，Gemini 自動解析成 document KB 條目
> - 2026-05-02（更新）：LLM 升級為 `gemini/gemini-2.5-flash`（文字 + Vision）+ `gemini-embedding-2`（dim=1536）；MAX_PUSH_PER_DAY 調整為 50
> - 2026-05-02（更新）：`brand_brief` 問句污染問題修復（DB 清除 + `context_brief_manager.py` 雙重保護）；草稿品質確認無幻覺
> - 2026-05-02（更新）：photo_content pipeline 全段驗證（analyze-photo → retrieve-context → generate-drafts → notify-approval 均 200 OK）；老闆 LINE 收到草稿 Flex Message，內容與 LLM 輸出一致
> - 2026-05-02（更新）：`Dispatch failed for workflow photo_content` warning 根因確認 — 首次 dispatch 因瞬間連線抖動失敗（`httpx.HTTPError` 訊息為空），存入 `kachu_deferred_dispatches`；Scheduler 5 分鐘後重試成功（status=dispatched），不影響草稿生成與 LINE 通知；`kachu_workflow_runs` 有完整記錄，**非 code bug，無需緊急修復**
> - 2026-05-02（更新）：文件口徑修正 — Google OAuth smoke 驗證屬內部開發驗證；在 GBP 審批完成前，不應將 Google 串接列為正式 onboarding 或對外可用能力
> - 2026-05-02（更新）：boss tenant 已完成 Meta OAuth，`meta` connector 已寫入 DB，當前綁定粉專為 `四時循養堂（原坐骨新經）`（`fb_page_id=940149472511909`）；目前 `ig_user_id` 尚未取得，因此 Instagram 仍待補齊商業帳號連結後重新授權
> - 2026-05-02（更新）：Meta `publish-content` 手動 smoke 已成功發到 Facebook，audit 記錄 `publish_succeeded`，且 Facebook 頁面可見貼文；但這仍不等於 photo_content approval-first workflow 已完整驗證 through postback
> - 2026-05-02（更新）：老闆三句手動驗證結果已寫庫追查。第一句諮詢題正常走 consultation；第二句「最近流量掉很多」本輪未看到 `ga4_report` workflow 或 clarify push 證據，而是以 consultation reply 結束；第三句「幫我更新這項資訊：我們今天公休」已建立 `knowledge_update` workflow run，但截至目前尚未在 DB 看見對應 approval task / audit event / push log，需續查 approval handoff
> - 2026-05-02（更新）：**三件事完成** — (1) 部署：step1+2 重構（clarify_question LLM 生成取代 quickReply buttons）推上 production，Docker image 重建完成，health 200 OK；(2) Embeddings 修復：production DB 原有 14 筆 `embedding IS NULL`，透過容器內腳本呼叫 `gemini-embedding-2` 全數補齊 1536 dims，驗收 COUNT=0；(3) Approval 鏈路：代碼審查確認鏈路完整（`flex_builder` 格式 ↔ `parse_qs` 解析一致），`AgentOSClient.get_pending_approval_id_for_run()` 在 production 容器直接測試回傳有效 approval_id，`decide_approval()` endpoint 存在且可呼叫。
> - 2026-05-02（更新）：後續 root cause 已確認。`knowledge_update` 在 AgentOS 端其實已停在 `waiting_approval`，只是 Kachu 未明確用 LINE `message_id` 建 idempotency key，重送相同句子時會重連舊 pending run，造成本地 DB 缺少新的 approval / push / audit side effects；本地已補上 `line_message_id` 傳遞與 idempotency 修補，並新增 regression tests。另，顯式品牌知識吸收 cue 已再收窄，避免歷史 `document` 污染模式重現
> - 2026-05-02（更新）：**WF1 photo_content 完整鏈路全段驗證通過** — 老闆 LINE 傳照片 → Kachu 分析+生成草稿 → LINE Flex 確認 → 老闆按「🚀 立即發布」→ ApprovalBridge → AgentOS decide_approval → publish-content → FB 頁面實際出現圖文貼文（四時循養堂（原坐骨新經），`fb_page_id=940149472511909`）。至此 WF1 FB 鏈路全段 end-to-end 驗證完成
> - 2026-05-02（更新）：Meta OAuth 新增 3 個 scope（`read_insights`、`pages_manage_engagement`、`instagram_manage_comments`）並重新授權完成（boss tenant，四時循養堂（原坐骨新經））；新增第十四節「Meta Insights 與留言管理驗證」，對應 `MetaClient` 新增 6 個方法與 8 個 tool endpoints 實作
> - 2026-05-03（更新）：**WF1 photo_content LINE 一次性排程發布驗證通過** — approval 卡已改為「立即發布 / 排程發布 / 我要修改 / 先不用」；老闆於 LINE 輸入排程時間後，Kachu 先覆述再確認，建立持久化 scheduled publish 記錄，之後由 `LINE Scheduled Publish Dispatch` 準時發文。另，production 已修正排程貼文缺圖問題：approval 建立時即持久化 preview image_url，排程流程也會補齊舊資料，並已回填 12 筆舊 pending approvals。
> - 2026-05-03（更新）：LINE 觸發鏈補充驗證修正：老闆傳「幫我寫一篇 Google 商家動態，主題是夏季養生提醒」時，Kachu 正確回 Google 商家動態草稿卡，支持 `google_post` intent；但老闆傳「你好」時，Kachu 回成品牌/流量策略建議，不能視為 `general_chat` 已驗證通過，該項需重測與修正。
> - 2026-05-03（更新）：`general_chat` 已於 production 重測通過。small_talk 路由修補部署後，老闆傳「你好」與「早安，今天辛苦了」皆回一般寒暄，且不再誤走 consultation / BusinessConsultant 路徑。
