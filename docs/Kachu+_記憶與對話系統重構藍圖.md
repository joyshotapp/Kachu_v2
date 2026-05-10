# Kachu+ 記憶與對話系統重構藍圖

版本：0.1 Draft  
日期：2026-05-10  
定位：以 Kachu_v2 為基線，為 Kachu+ 定義比 v2 更完整的記憶、檢索、理解與回應架構

最新實作紀錄：見 `docs/2026-05-10_本波新增與增強實作紀錄.md`

---

## 1. 為什麼要重構

這次 production 事件暴露的不是單一 bug，而是兩條主鏈都不夠成熟：

1. 知識吸收有進步，但目前仍偏向「存進去」，還沒有做到「可穩定取回、可跨任務重用、可持續內化」。
2. 對話理解目前仍偏向「先路由、再回答」，而不是「先理解上下文與狀態，再決定怎麼做」。

如果產品定位是 SMB 的 AI 商業夥伴，使用者應該要有兩個直接感受：

1. Kachu 越聊越懂我。
2. Kachu 給的建議、草稿與回應越來越準。

達成這個體驗的前提不是只換更強模型，而是把對話、知識、偏好與任務狀態納入同一套可檢索記憶系統。

---

## 2. 設計原則

### 2.1 保留 v2 正確骨架

Kachu_v2 有三個方向是正確的，Kachu+ 應沿用：

1. 對話紀錄本身是記憶來源，不只是審計資料。
2. 記憶要分層，而不是所有東西都塞成同一種 knowledge entry。
3. brand brief / owner brief 必須由知識與對話共同生成，而不是只依賴 onboarding 靜態欄位。

Kachu_v2 可直接參考的實作錨點：

1. raw conversation 入庫：/Users/yuchuchen/Desktop/Kachu_v2/src/kachu/onboarding/flow.py
2. 四層記憶：/Users/yuchuchen/Desktop/Kachu_v2/src/kachu/memory/manager.py
3. brief 生成：/Users/yuchuchen/Desktop/Kachu_v2/src/kachu/context_brief_manager.py
4. 對話摘要與文件事實抽取：/Users/yuchuchen/Desktop/Kachu_v2/src/kachu/conversation_context.py
5. 文件吸收與 derived facts：/Users/yuchuchen/Desktop/Kachu_v2/src/kachu/knowledge_capture.py

### 2.2 不直接複製 v2

Kachu_v2 有幾個地方不應原樣沿用：

1. preference 與 episodic memory 折疊進 knowledge table，長期會讓 schema 意義變混。
2. semantic retrieval 雖然存在，但仍偏輕量，沒有把 task state、近期對話與 structured facts 一起納入同一個 retrieval 計畫。
3. intent classification 仍以 keyword fast-path 為主，LLM 只做補強，對 follow-up 與跨輪承接仍不足。
4. 對 boss 與 customer 的 retrieval / response path 尚未完全統一。

### 2.3 Kachu+ 的提升目標

Kachu+ 不應只是比 v2 多一個網站吸收功能，而應升級成：

1. conversation-native：每輪對話都可被吸收、提煉與回用。
2. state-aware：任務、待確認草稿、最近修正、最近策略方向都屬於可查詢狀態。
3. hybrid-retrieval：structured facts、semantic memory、recent dialogue、task state 共同參與檢索。
4. learning-closed-loop：使用者每次修稿、否決、補充與追問都會讓系統變準。

---

## 3. 現況診斷

### 3.1 Kachu+ 已有基礎

目前 Kachu+ 已經有以下可用基礎：

1. 網站吸收與結構化存檔：src/kachu_plus/website_knowledge.py
2. onboarding 與品牌基本資料累積：src/kachu_plus/onboarding/flow.py
3. execute task tracking：src/kachu_plus/persistence/tables.py、src/kachu_plus/persistence/repository.py
4. preference / episodic / brief：src/kachu_plus/learning.py
5. LINE 對話主入口：src/kachu_plus/line/webhook.py

### 3.2 Kachu+ 目前不足

目前缺口集中在四點：

1. 沒有 conversation layer。現在只有 webhook raw payload 與部分 knowledge entry，沒有一張真正可查詢的逐輪對話表。
2. consult 回答沒有強制帶入記憶。LLMConsultant 目前只吃 tenant_name、industry_type、message。
3. intent router 仍是 keyword-first。對 follow-up、修正、承接上一輪、狀態查詢等語意過於脆弱。
4. retrieval 仍偏 category / recent-first，不是 query-aware 的 hybrid retrieval。

---

## 4. 目標架構

### 4.1 五層結構

Kachu+ 的目標架構定義為五層，而不是 v2 的四層：

1. Layer A: Raw Conversation Memory
2. Layer B: Structured Knowledge Memory
3. Layer C: Preference Memory
4. Layer D: Episodic / Outcome Memory
5. Layer E: Derived Shared Context

差異在於第 E 層是顯式存在的，不只是 runtime 拼接後的暫存物。

### 4.2 Layer A: Raw Conversation Memory

用途：保存每一輪真實對話，支援短期回顧、摘要生成、後續升格、審計與離線評測。

建議新表：kachu_conversations

建議欄位：

1. id
2. tenant_id
3. actor_role：boss | ai | customer | platform
4. channel_type：line | web | meta | google
5. conversation_kind：onboarding | boss_command | boss_consult | customer_faq | follow_up | system
6. content_text
7. source_message_id
8. related_task_id
9. related_run_id
10. metadata_json
11. created_at

規則：

1. 所有 boss/customer/ai 關鍵輪次都應寫入。
2. webhook raw payload 繼續保留作審計，但不替代 conversation table。
3. 後續所有 summary、follow-up 判定與 memory promotion 都以這張表為起點。

### 4.3 Layer B: Structured Knowledge Memory

用途：保存可持續被引用的品牌與營運事實。

來源：

1. onboarding 回答
2. 老闆主動補充
3. 官網與文件吸收
4. 從 conversation promotion 升格出來的穩定事實
5. 外部平台資料

建議保留現有 kachu_knowledge_entries，但補上這些治理能力：

1. source_ref / source_conversation_id
2. status：active | superseded | stale | conflict | archived
3. confidence_score
4. valid_from / valid_until
5. supersedes_entry_id

網站吸收不再是特例，而是 structured knowledge 的其中一種 source_type。

### 4.4 Layer C: Preference Memory

用途：記住老闆怎麼修稿、偏好什麼語氣、不喜歡什麼寫法。

建議保留獨立表，不回退到 v2 折疊進 knowledge entries 的做法。

新增來源不只來自 edit draft，也包含：

1. 老闆明講的語氣要求
2. 老闆否決某種表述
3. 老闆重複偏好的 CTA / platform / tone

### 4.5 Layer D: Episodic / Outcome Memory

用途：記住哪些任務最後成功、哪些建議被接受、哪些方向被拒絕。

來源：

1. approval decision
2. publish result
3. suggestion accepted / rejected
4. boss follow-up satisfaction signals

這層應與 task / run 狀態打通，讓系統知道「你上次給這個老闆的類似做法最後是否有效」。

### 4.6 Layer E: Derived Shared Context

用途：把前四層的內容壓縮成可直接注入 prompt 的短中期共享上下文。

建議維持並擴充現有 brief 機制：

1. brand_brief
2. owner_brief
3. conversation_summary_brief
4. active_task_brief
5. customer_service_brief

這一層是供 runtime 高頻讀取，不是原始事實來源。

---

## 5. 核心能力升級

### 5.1 從 route-first 升級為 state-first

Kachu+ 不應直接用訊息文字進 intent router，而應先走一層 state resolution：

1. 這句話是不是承接上一輪？
2. 目前有沒有 active task / pending approval / recent draft？
3. 這句話是在補資料、追進度、修正前文、問策略、還是下新指令？

建議新增流程：

1. ResolveDialogueState
2. RetrieveContextPlan
3. AdjudicateIntent
4. Execute / Consult / Clarify

其中只有第 4 步延續現在 BossRouteMode 的概念。

### 5.2 從單一路徑檢索升級為 hybrid retrieval

每次回應前，不直接抓固定數量 highlights，而是產出 retrieval plan：

1. recent_conversations：最近 6 到 10 則高度相關對話
2. active_task_state：最近 task / run / approval 狀態
3. persistent_knowledge：品牌與營運事實
4. preference_examples：偏好修稿樣本
5. episodes：類似任務最近結果

排序邏輯：

1. structured exact match 優先
2. active state 優先
3. semantic similarity 次之
4. recency 作為 tie-breaker

### 5.3 對話吸收要有 promotion 規則

不是所有對話都升格成長期知識。建議加一層 ConversationMemoryPromoter：

1. 明確品牌事實：升格到 structured knowledge
2. 明確語氣或偏好：升格到 preference memory
3. 任務結果與反應：升格到 episodic memory
4. 其餘保留在 raw conversation，定期摘要

### 5.4 consult 路徑必須帶 context bundle

之後所有 consult 回答都必須帶入：

1. brand_brief
2. owner_brief
3. recent_conversations
4. active_task_brief
5. relevant_knowledge
6. recent_preferences

否則再強的模型也只是在資訊不足下生成一般答案。

---

## 6. 與 v2 的對照

### 6.1 直接移植

以下設計可直接借力：

1. ConversationTable 的概念與 repo API
2. KnowledgeCaptureService 的 derived facts 抽取
3. ContextBriefManager 的 owner / brand brief 思想
4. build_conversation_digest 的摘要方法
5. MemoryManager 的四層語意分工

### 6.2 必須重寫

以下不能照搬：

1. 把 preference / episode 折進 knowledge entries 的 schema 策略
2. 僅靠 keyword + 補充 LLM 的 intent 架構
3. 未把 execute task state 納入 retrieval 的回答流程
4. boss / customer 各走各的 context 策略

### 6.3 Kachu+ 要比 v2 更好的地方

1. 網站內容是正式 memory source，不是附件功能。
2. task state 是一級檢索來源，不再靠臨時邏輯補洞。
3. 對話會被持續吸收，而不是只在 onboarding 或編輯草稿時學習。
4. retrieval 要同時吃 structured、semantic、state、preference、episode。
5. 需要有明確評測集來量化「是否越來越懂使用者」。

---

## 7. 實作切片

### Phase 1: 對話入庫與狀態打底

目標：把目前缺失最大的 raw conversation layer 建起來。

工作項目：

1. 新增 kachu_conversations migration
2. 在 line webhook 寫入 boss / ai / customer 對話紀錄
3. 對 execute task record 增加 conversation 關聯欄位
4. 補 repo CRUD：save_conversation、list_recent_conversations、list_related_conversations

完成定義：

1. 任一真實 LINE 對話可在 DB 重建最近 20 輪
2. 可查到某個 task 對應的前後文

### Phase 2: brief 與 consult context 升級

目標：讓 consult 不再像泛用 chatbot。

工作項目：

1. 新增 conversation_summary_brief、active_task_brief
2. 重寫 ContextBriefManager，納入 recent conversations 與 task state
3. 改 LLMConsultant.build_reply 介面，輸入 context bundle，而不是只有 tenant_name / industry_type / message

完成定義：

1. consult prompt 固定帶入 brand + owner + recent dialogue + active task
2. 對同一使用者的連續提問，回覆內容能明顯承接上下文

### Phase 3: retrieval 與 promotion

目標：讓對話與知識真正內化。

工作項目：

1. 新增 ConversationMemoryPromoter
2. 將對話自動升格為 knowledge / preference / episode
3. 實作 retrieval plan composer
4. 導入 embedding 或 hybrid search 抽象層

完成定義：

1. 老闆補充過的品牌事實可被後續 consult / content generation 正確使用
2. 老闆連續修稿後，下一版草稿能反映偏好

### Phase 4: intent 與 follow-up 理解升級

目標：處理真實多輪對話，而不只是單句命令。

工作項目：

1. 新增 dialogue state resolver
2. 以 recent conversations + active task 決定 follow-up intent
3. 模糊 case 才交給 LLM adjudicator
4. 保留 keyword fast-path 作為性能優化，不再作為唯一真相

完成定義：

1. 草稿進度、修正、承接上一輪的追問不再掉到 clarify
2. 含混但有上下文可推論的句子可穩定落到正確路徑

### Phase 5: 評測與閉環

目標：把「越來越懂你」變成可驗證能力，而不是主觀感覺。

工作項目：

1. 建 boss multi-turn eval set
2. 建 customer FAQ retrieval eval set
3. 建 memory promotion precision / recall 指標
4. 建 consult groundedness 與 task follow-up success 指標

建議 KPI：

1. follow-up routing accuracy
2. consult groundedness pass rate
3. retrieval hit rate
4. owner preference reuse rate
5. task status follow-up success rate

---

## 8. 與產品模組順序的對齊

這份藍圖不能脫離 Kachu+ 既有的模組順序單獨落地，否則會把基礎設施做成獨立支線。依照目前產品定義文件的順序，應這樣對齊：

### 模組一：Onboarding + LINE

這一階段必做：

1. conversation table 與 webhook 寫入
2. onboarding 對話進 raw conversation
3. onboarding 內容 promotion 到 structured knowledge
4. 初版 brand_brief / owner_brief 改由 onboarding + conversation 共同生成

這一階段先不要做：

1. 完整 semantic retrieval
2. 全量 multi-source hybrid ranker

原因：模組一的核心是先讓系統記得使用者說過什麼，並讓 onboarding 不再是一次性填表。

### 模組二：Connector 與內容生成

這一階段必做：

1. consult context bundle
2. active_task_brief
3. preference memory 注入生成流程
4. 任務與 execute state 納入 retrieval plan

這一階段的目標不是更多指令，而是讓內容生成開始吃到真正個人化上下文。

### 模組三：Customer Memory / FAQ / 標籤

這一階段必做：

1. customer conversation memory 接入
2. customer FAQ retrieval 改為 hybrid retrieval
3. customer-facing knowledge 與 boss-facing knowledge 共用底層記憶層，但保留不同 retrieval policy

這一階段是 Kachu+ 從老闆助手進一步變成前後台共用記憶系統的關鍵。

### 模組四：Durable Suggestions

這一階段必做：

1. episodic / outcome memory 與 suggestion lifecycle 打通
2. suggestion acceptance / rejection / edit history 回灌 episode
3. 類似情境建議優先取用歷史有效模式

### 模組五：Approval 與 Learning Loop

這一階段必做：

1. approval 結果變成 learning signal
2. memory promotion precision / recall 評測
3. follow-up routing 與 consult groundedness 評測常態化

結論：

1. conversation memory 必須從模組一就開始。
2. hybrid retrieval 可以延後到模組二、三逐步長出來。
3. learning loop 不該等到模組五才開始存資料，而是模組五才正式把資料閉環變成評測與優化機制。

---

## 9. Schema 與遷移準則

為避免重蹈 v2 記憶類型混疊的問題，schema 調整必須遵守以下原則：

1. raw conversation、structured knowledge、preference、episode 不共用同一張主表語意。
2. derived brief 可以是快取或投影，但不能作為唯一事實來源。
3. 所有 memory promotion 都必須可追溯 source，至少能回指 conversation_id、knowledge source 或 task/run。
4. 若某項知識被新事實覆蓋，要用 supersede / status 表達，不直接硬覆寫舊資料。

建議第一批 migration：

1. 新增 kachu_conversations
2. 為 knowledge entries 增加 source_conversation_id、status、confidence_score、supersedes_entry_id
3. 為 execute task record 增加 related_conversation_id
4. 視需要新增 conversation_summary / retrieval cache 類型欄位到 context briefs

---

## 10. 對應到當前 codebase 的落點

### 現有檔案需要升級

1. src/kachu_plus/line/webhook.py
2. src/kachu_plus/intent_router.py
3. src/kachu_plus/learning.py
4. src/kachu_plus/services.py
5. src/kachu_plus/tools_router.py
6. src/kachu_plus/persistence/tables.py
7. src/kachu_plus/persistence/repository.py

### 建議新增檔案

1. src/kachu_plus/conversation_memory.py
2. src/kachu_plus/retrieval_plan.py
3. src/kachu_plus/dialogue_state.py
4. src/kachu_plus/memory_promotion.py
5. src/kachu_plus/consult_context.py

---

## 11. 最小可行落地順序

若要在不重翻整個系統的前提下快速改善體驗，建議順序如下：

1. 先做 Phase 1：conversation 入庫
2. 再做 Phase 2：consult context bundle
3. 接著做 Phase 4 的 follow-up state resolver
4. 最後才做完整 promotion + hybrid retrieval

理由：

1. 對使用者體感提升最快的是「Kachu 記得我剛剛說過什麼」。
2. 對 production 風險最低的是先加 memory 與 context，不是先全面改 router。
3. 對後續評測最有幫助的是先把 raw conversation 收好。

---

## 12. 定論

Kachu+ 的正確方向不是退回 v2，也不是只在現有 Kachu+ 上補更多關鍵字規則。

正確方向是：

1. 沿用 v2 的對話入庫與分層記憶思想
2. 升級成 conversation-native 的正式架構
3. 讓知識、偏好、episode、task state 與 recent dialogue 共同參與 retrieval
4. 讓理解層從 route-first 升級為 state-first

只有這樣，Kachu+ 才有機會真正做到「越聊越懂你、越用越準」。