# Kachu+ 完整對話體驗系統設計

版本：1.0
日期：2026-05-12
適用範圍：LINE 老闆對話主流程、諮詢回覆、澄清追問、跨輪承接、品牌與產業脈絡注入

## 1. 目標

本系統不是只要「有回應」，而是要滿足以下四個同時成立的條件：

1. 每次回覆都要切中老闆這一則訊息真正的問題、要求或顧慮。
2. 回覆必須有商業專業度，能指出判斷依據、風險或下一步。
3. 回覆必須有溫度，語氣像可信任的商業夥伴，不是冷硬客服。
4. 回覆必須承接 tenant 自己的品牌、產業、最近對話與進行中的任務，不能像第一次見面。

## 2. 非 MVP 標準

若要支撐大量上線，本系統必須避免以下常見退化：

1. 把大量不同問題回成同一句固定澄清。
2. 忽略上一輪未完成的上下文，造成答非所問。
3. 只靠產業模板說空話，沒有引用 tenant 自身脈絡。
4. 遇到模糊問題時過度追問，沒有先做最小有用回答。
5. 遇到情緒、抱怨、焦慮、急迫時沒有先接住使用者。
6. 把諮詢問題誤送進執行流程，或把執行命令誤回成建議。

## 3. 能力需求

### 3.1 訊息理解

每則 boss 訊息必須被理解成下列結構：

- primary_mode：execute / consult / clarify
- intent_label：具體任務或諮詢主題
- response_strategy：direct_answer / ask_targeted_question / capability_overview / greeting / empathy_clarify / execute
- user_goal：老闆想完成什麼
- missing_slots：還缺哪些必要資訊
- emotional_signals：急、擔心、不滿、沮喪、友善招呼等
- referenced_context：是否承接上一輪任務、最近對話、上傳素材或草稿
- reply_outline：給回覆生成器的明確指令

### 3.2 回覆規劃

在真正生成文字前，系統必須先決定回覆策略：

1. 若可直接回答，優先直接回答，再補一個下一步。
2. 若只缺一個關鍵槽位，先給部分判斷，再問一個精準問題。
3. 若老闆是在問能力範圍，回 capability overview，不進 generic clarify。
4. 若老闆只是招呼或試探在不在，回 greeting，不呼叫昂貴顧問鏈。
5. 若訊息帶情緒或抱怨，先接住情緒，再回到問題本身。
6. 若為延續上一輪任務的 follow-up，優先承接 active task 與 recent thread。

### 3.3 上下文

回覆必須至少考慮以下上下文來源：

1. tenant brand brief
2. tenant owner brief
3. active task brief
4. recent conversations thread
5. relevant knowledge
6. recent preferences
7. customer brief
8. retrieval plan 中的 active_task_state

### 3.4 對話風格

每次回覆應符合：

1. 先回應老闆這句話本身，不先背模板。
2. 有結論，但不武斷。
3. 句子短而清楚，不堆疊行銷空話。
4. 若要追問，只問一個最有區分力的問題。
5. 若已知資訊足夠，不得偷懶改問泛用澄清句。

## 4. 核心設計

### 4.1 ConversationResponsePlan

新增一個結構化計畫物件，用來橋接 intent router 與最終回覆。

必要欄位：

- mode
- intent_label
- response_strategy
- user_goal
- context_summary
- reply_directive
- consult_reply
- clarify_question
- confidence
- reasoning_signals

### 4.2 Response Planner

新增 planner 層，責任如下：

1. 吃進原始訊息、router decision、dialogue state、tenant brief、retrieval plan。
2. 產出回覆策略，而不是只回三分流 mode。
3. 生成針對情境的 clarify question，而不是一律固定句。
4. 生成 capability/greeting/empathy 等非 LLM 快速路徑。
5. 為 CONSULT 類型提供結構化 reply directive，讓 LLM 回得更貼題。

### 4.3 Clarify 規則

clarify 不等於 generic fallback。只有在系統無法安全直答且缺少關鍵資訊時才用。

優先級：

1. 局部回答 + 問一個關鍵問題
2. 指出兩種可能理解，請老闆選一個
3. 真的資訊太少時才用通用澄清

### 4.4 LLM Consult 規則

consult 回覆生成器必須接收 planner 產生的 directive，至少包含：

1. 老闆真正問題
2. 回覆策略
3. 要先接住的情緒
4. 必須引用的 tenant/context 訊號
5. 回覆格式要求：結論、理由、下一步

### 4.5 觀測性

每次 boss 對話都要能追溯：

1. router 初判
2. planner 最終策略
3. 是否走 fast-path
4. LLM 是否被呼叫
5. delivery audit 結果

## 5. 驗收標準

### 5.1 功能驗收

1. 招呼不再進 generic clarify。
2. 能力詢問不再進 generic clarify。
3. 常見模糊句能回具體 clarify question。
4. 策略諮詢仍可呼叫 consultant，不被 fast-path 吃掉。
5. follow-up 訊息能承接 active task 或 recent context。

### 5.2 對話體驗驗收

1. 回覆要先回應訊息核心，再進一步延伸。
2. 不得出現大量不同輸入得到相同回覆。
3. 澄清句要能反映具體語境，例如流量、客人、評論、貼文、店務。
4. 顧問回覆須引用 context，而非只講產業空話。

### 5.3 測試驗收

至少補齊以下測試：

1. greeting
2. capability overview
3. review-focused clarify
4. traffic-focused clarify
5. customer-focused clarify
6. consultant path with planner directive
7. webhook path confirms strategy-specific reply

## 6. 可行性評估

### 6.1 可行

本次改造可行，因為現有系統已具備：

1. 穩定的 LINE webhook 主流程
2. router / dialogue state / consult context / consultant 四個現成接點
3. 記憶與 brief 機制
4. 已有測試基礎與 delivery audit

### 6.2 風險

1. 若只改 prompt 不改 planner，無法根治答非所問。
2. 若 planner 不接 active task / retrieval plan，只會變成另一層模板。
3. 若沒有測試保護，之後任何 router 調整都會再退化成固定回覆。

### 6.3 本次完整實作範圍

本次直接實作下列項目：

1. 新增 ConversationResponsePlan 結構
2. 新增 planner 模組
3. planner 接入 webhook boss text 主流程
4. 動態 clarify question 與 empathy/greeting/capability 快速路徑
5. consult reply directive 注入 LLMConsultant
6. 補齊 intent/router/response path 測試

## 7. 上線前最低要求

若要承受大量使用，至少要做到：

1. 所有新增測試通過
2. 主要 boss 對話路徑不可再有固定澄清濫用
3. 所有對話策略與 delivery audit 可追蹤
4. production smoke test 至少涵蓋 greeting、capability、consult、clarify 四類
