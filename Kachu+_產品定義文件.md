# Kachu+ 產品定義文件

版本：0.3 Draft  
日期：2026-05-08  
撰寫基礎：深度審閱 Kachu_v2、Super8、Cresclab（OneLoop）、AgentOS 四個專案後起草  
修訂說明：v0.3 明確定義 greenfield 開發策略，補上四個專案的資源借用原則與對照路徑

---

## 0.5 RD 開發導讀（從這裡開始）

> 本文件共 1050 行。本章節是 RD 的入口，把所有開發需要的資訊濃縮成三層：「先讀什麼、開發順序、每個模組看哪幾個錨點」。

---

### 最短必讀路徑（開工前的順序）

1. **§4.3 V1 完整使用者旅程**（第 206 行）— 先把商家會走過的路徑跑一遍，建立畫面感，之後所有技術決策才有對齊點
2. **§7.4 資料層核心 tables**（第 520 行）— schema 先確認，任何功能的邊界都從 table 推導
3. **§7.5 V1 工程切片計畫**（第 602 行）— 五個模組的開發任務、直接移植的程式碼、需要新建的部分、完成定義
4. **§12 四層地基**（第 862 行）— 每個引用的原始碼檔案、具體看什麼、直接得到什麼；開發時用來查找，不用猜

**不需要一開始全讀**的章節：§1–3（產品願景/競品/護城河）是背景，§8–11（護城河/競品/北極星/開放問題）是決策層，不影響 v1 的程式實作。

---

### 模組開發順序與依賴關係

```
模組一：Onboarding + LINE 指揮介面
  ↓ （webhook + intent router 通了之後）
模組二：品牌陣地連接器 + 內容生成        模組三：顧客記憶層
  （可與模組三並行開發）                    （可與模組二並行開發）
         ↓                                          ↓
              模組四：主動建議引擎（依賴模組二「評論偵測」+ 模組三「沉睡偵測」）
                          ↓
              模組五：學習閉環（依賴模組一到四都有真實資料流入）
```

**並行說明**：模組二和三的 schema 設計可同時進行，但模組四要等模組二的 `google_post_pipeline` 和模組三的 `kachu_customer_profiles` 都有資料後才能端對端測試。

---

### 模組內任務順序（可直接開工的具體清單）

> 每列代表一個可獨立交付的任務，`#` 欄是建議的執行序。「解鎖」欄說明完成後才能做什麼，是判斷能否並行的依據。

**模組一：Onboarding + LINE 指揮介面（所有模組的前提，優先完工）**

| # | 做什麼 | 完成條件 | 解鎖 |
|---|---|---|---|
| 1-1 | 建 `kachu_tenants` + webhook config table，跑 migration | migration 成功，schema 與 §7.4 一致 | 所有後續模組 |
| 1-2 | LINE webhook endpoint（signature 驗證 + multi-tenant routing） | 偽造 tenant 或 signature 錯誤時拒絕；正確 tenant 收到事件 | 1-3 |
| 1-3 | Intent router（`BossRouteMode`: EXECUTE / CONSULT / CLARIFY） | 100 筆測資分類準確率 ≥ 90%（參考 `intent_router.py` 的 `classify_text`） | 1-4、模組二/三可並行 |
| 1-4 | Onboarding flow（5-step + redo 支援，移植 `_BOT_MESSAGES` + `_detect_redo_step`） | 完整走完 5 步；「上一題」正確回退且不遺失已存資料 | 模組三冷啟動資料 |
| 1-5 | 三種 intent 的基礎回應路徑（EXECUTE → AgentOS task；CONSULT → LLM；CLARIFY → 追問） | 每條路徑有可觀察的輸出（log 或回覆訊息） | 模組二端對端 |

**模組二：品牌陣地連接器 + 內容生成（1-5 完成後才能端對端，可與模組三並行）**

| # | 做什麼 | 完成條件 | 解鎖 |
|---|---|---|---|
| 2-1 | AgentOS `WorkflowService` 整合（`create_task` + `run_task`，`current_run_id` resume 邏輯） | task 可建立、run 狀態可追蹤、approval timeout 可觸發 | 所有 pipeline |
| 2-2 | Google 評論 adapter（fetch review list，credential 管理） | 可取回真實評論；credential 過期時回 graceful error | 2-3 |
| 2-3 | `review_reply_pipeline`（照 `build_kachu_review_reply_plan()` 格式，approval_timeout 6h） | 草稿推送 LINE、approval gate 觸發、商家確認後工作流 resume | 2-4 |
| 2-4 | `approval_bridge`（`handle_postback` → AgentOS `decide_approval`，含 edit flow `complete_edit_and_approve`） | 商家在 LINE 按確認/修改/拒絕，工作流正確 resume 或 cancel | 2-5 |
| 2-5 | `google_post_pipeline`（照 `build_kachu_google_post_plan()` 格式，approval_timeout 48h） | 商家確認後發布 GBP 動態；publish step 標記 IRREVERSIBLE，二次確認通過才執行 | 模組四評論偵測 |

**模組三：顧客記憶層（1-3 完成後可並行，schema 設計可與模組二同時進行）**

| # | 做什麼 | 完成條件 | 解鎖 |
|---|---|---|---|
| 3-1 | 建三層 identity tables（`kachu_customer_profiles` / `kachu_channel_entities` / `kachu_profile_links`） | migration 成功；`(tenant_id, channel_type, external_user_id)` unique constraint 可觸發 | 所有顧客記憶功能 |
| 3-2 | LINE 來訊時自動建立/查找 profile（R2：同 LINE user_id 不重複建 profile） | 相同 user_id 發兩次訊，`kachu_customer_profiles` 仍只有一筆 | 3-3 |
| 3-3 | 手動標籤 CRUD（R8：刪標籤不破壞歷史 timeline） | tag 刪除後 timeline 查詢仍完整 | 3-4 |
| 3-4 | 每日排程計算 `sleep_since_days`（按 `last_interaction_at` + tenant 的 `sleep_threshold`） | 跑完後欄位數值正確；排程 process 重啟後仍繼續 | 3-5、模組四沉睡偵測 |
| 3-5 | 沉睡查詢 intent handler（「哪些客人超過 X 天沒來」） | 回傳列表正確；R6 黑名單/退訂 profile 不出現在結果中 | 模組四 |

**模組四：主動建議引擎（2-5 + 3-4 完成後才能端對端）**

| # | 做什麼 | 完成條件 | 解鎖 |
|---|---|---|---|
| 4-1 | 建 `kachu_suggestions` table（§7.4 schema） | migration 成功 | 所有建議卡功能 |
| 4-2 | `_detect_nudge()` 移植 + 新增沉睡偵測（`NUDGE_NO_POST` / `NUDGE_NEGATIVE_REVIEW` / 沉睡顧客） | 三類場景皆可在測試資料下正確觸發 | 4-3 |
| 4-3 | Durable job queue（每日 recurring trigger，process 重啟後繼續執行） | kill process 再重啟，排程任務不遺失 | 4-4 |
| 4-4 | 建議卡生成 + LINE 推送（建議卡 schema 對應 §6.2） | 正確 tenant 在 LINE 收到建議卡 | 4-5 |
| 4-5 | `KachuExecutionPolicyResolver.resolve()` 移植（per-tenant `recent_acceptance_rate` 動態調整 approval timeout） | 高信任商家 6h timeout；低信任商家加 direction check | 模組五 |

**模組五：學習閉環（模組一到四都有真實資料後）**

| # | 做什麼 | 完成條件 | 解鎖 |
|---|---|---|---|
| 5-1 | `MemoryManager` 移植（四層記憶架構，`store_preference` + `get_preference_examples`） | Layer 3 preference 可存 diff、取出最近 3 筆 | 5-2 |
| 5-2 | `ContextBriefManager.refresh_briefs()` 移植（TTL 30 天，三份 brief：brand / owner / customer） | TTL 過期後下次呼叫重新產生；refresh 為非同步 | 5-3 |
| 5-3 | `PostTaskReviewService` 移植（`after_preference_update` + `after_approval_decision`） | 修改草稿後自動記錄 diff；決策後記錄 episode；兩者都觸發非同步 brief refresh | 5-4 |
| 5-4 | Brief 注入 LLM call 路徑（三份 brief 注入對應觸點，見 §7.6.3） | 有歷史偏好的 tenant，生成內容的風格與早期明顯不同（人工確認） | — |

---

### 每個模組的三個開發錨點

| 模組 | ① 任務定義 | ② 可直接用的程式碼 | ③ 完成定義（驗收標準） |
|---|---|---|---|
| **一** Onboarding + LINE | §7.5 模組一（第 608 行） | `Kachu_v2/src/kachu/onboarding/flow.py`、`intent_router.py`、`line/webhook.py` | 5-step onboarding 完整跑通；指令分類 EXECUTE/CONSULT/CLARIFY 正確 |
| **二** 品牌陣地 + 內容生成 | §7.5 模組二（第 623 行） | `AgentOS_real/kachu_workflows/review_reply_pipeline.py`、`google_post_pipeline.py`、`approval_bridge.py`、`industry_playbook.py` | 評論草稿推送商家確認後自動發布；失敗情境有明確回報路徑 |
| **三** 顧客記憶層 | §7.5 模組三（第 639 行） | schema 參考 `Cresclab/04_core_schema_spec.md`（三層 identity 設計）；業務規則看 §12.2 R2/R3/R6/R8 | 手動貼標籤；每日排程計算 `sleep_since_days`；沉睡查詢回傳正確列表 |
| **四** 主動建議引擎 | §7.5 模組四（第 657 行） | `Kachu_v2/src/kachu/proactive_monitor.py`、`policy.py`、`post_task_review.py` | 每日自動掃描兩類場景；建議卡推送 LINE；商家確認後執行；7天回應率可查 |
| **五** 學習閉環 | §7.5 模組五（第 681 行） | `Kachu_v2/src/kachu/memory/manager.py`、`context_brief_manager.py`、`post_task_review.py` | 修改草稿後自動記錄 diff；30天後建議接受率高於前7天 |

---

### 開工前 Checklist

- [ ] §7.4 的七張 table 已確認可建（schema 沒有疑義）
- [ ] AgentOS 已在本地跑起來（`WorkflowService` 可呼叫）
- [ ] Kachu_v2 本地可執行（跑 `pytest` 確認 305 tests passing，了解移植基準）
- [ ] 每個模組的「直接移植的程式碼」欄位已閱讀過原始碼（不只看文件描述）
- [ ] §12 對應章節已看過：實作前先查，不要靠記憶猜函式名

---

## 0. 為什麼是「+」

### Kachu v2 的真實定位（先說清楚）

Kachu v2 的工作對象是**品牌對外的陣地**，LINE 是指揮介面：

```
LINE ──商家下指令──▶ Kachu v2 ──執行──▶ Google Business（發文、回覆評論）
                                  ──執行──▶ FB / IG（發文、分析洞察）
                                  ──執行──▶ GA4（報表摘要）
                                  ──執行──▶ 知識庫（更新 FAQ）
```

LINE 是「商家」的指揮工具，不是「顧客」的管理渠道。Kachu v2 沒有顧客名單、沒有顧客記憶、沒有主動觸達。

### Kachu+ 的升維：二合一，缺一都是殘缺

一人多角的 SMB 商家同時面對兩個問題：
- **「新客人找不到我」** → 品牌陣地管理（Google/FB/IG 沒人維護）
- **「老客人不回來」** → 顧客關係管理（沒有系統追蹤、沒時間手動聯繫）

這不是二擇一，而是同一個商家的完整痛點。只解決其中一個都是殘缺的產品。

Kachu+ 不是 Kachu v2 的功能迭代，而是抽象層的升維：

| | Kachu v2 | Kachu+ |
|---|---|---|
| 工作對象 | 品牌陣地（Google/FB/IG） | 品牌陣地 + 顧客關係，二合一 |
| 核心行為 | 執行商家已知的任務 | 管理商家的商業意圖 |
| 互動模式 | 商家主動指令 → 執行 | 有指令執行 + 沒指令主動建議 |
| 顧客資料 | customer_line_id，幾乎空白 | 完整顧客記憶層 + 動態分眾 |
| 學習機制 | 無 | per-tenant 持續學習，越用越懂你的生意 |
| LINE 的角色 | 指揮介面（商家用） | 指揮介面（商家用）+ 顧客渠道（顧客用）|
| 定位 | 工具 | AI-native 商家營運夥伴 |

---

## 1. 產品願景

> **Kachu+ 是一個比商家更懂「現在該做什麼」的 AI 營運夥伴。**

它不管理訊息，它管理商業意圖。  
它不等你下指令，它在你沒時間想的時候提醒你「上個月這時候這樣做有用，你現在要試嗎？」  
它不替你決定，它讓決定變得只需要一個「好」或「不用」。

目標是讓一個沒有行銷人員、沒有客服團隊的個人商家，做到以前只有企業才做得到的品牌經營與顧客關係管理。

---

## 1.5 雙迴圈飛輪（Kachu+ 的核心結構）

這兩個場景不是獨立功能，而是同一個商業飛輪的兩端：

```
        ┌─────────────────────────────────────┐
        │         品牌陣地管理                  │
        │  Google/FB/IG 發文、評論回覆、         │
        │  洞察分析、貼文建議                    │
        └──────────────┬──────────────────────┘
                       │
              新客人找到你、留下好評
                       │
                       ▼
              顧客第一次互動（LINE）
                       │
              進入顧客記憶層，建立 profile
                       │
                       ▼
        ┌─────────────────────────────────────┐
        │         顧客關係管理                  │
        │  沉睡偵測、找回老客人、                │
        │  預約提醒、個人化觸達                  │
        └──────────────┬──────────────────────┘
                       │
          老客人回來、再留評、推薦朋友
                       │
                       ▼
         ─────── 回到品牌陣地管理 ───────
```

**Kachu v2 已經驗證了上半圈（品牌陣地）的產品價值。**  
**Kachu+ 要在新架構上把上下半圈一起做對，讓飛輪真正可持續運作。**

兩個迴圈的資料天然互通：
- 一篇 Google 評論可以觸發「這位客人可能是 LINE 的某個 profile，要加個『好評顧客』標籤嗎？」
- 一位沉睡顧客的喚醒可以轉化為新的 Google 評論或 IG 口碑

---

## 1.6 開發策略：Greenfield，但不是閉門重做

Kachu+ 採取的是 **greenfield implementation**，不是 **greenfield thinking**。

意思是：

- **程式碼重建**：不直接沿用 Kachu v2 的單體執行模型、資料表設計與 AgentOS 綁定方式
- **產品學習繼承**：保留 Kachu v2 已被驗證的工作流、Super8 已整理好的業務規則、Cresclab 已成形的資料與事件設計
- **外部能力善用**：把四個專案當作 Kachu+ 的「已知資產庫」，不是各看各的參考文件

這個決策的原因不是排斥既有專案，而是避免把 Kachu+ 的核心能力綁進舊的抽象與技術債。

### 1.6.1 我們不做的事

- 不在 Kachu v2 上持續加需求，讓新產品被舊 runtime 反向定義
- 不把 AgentOS 視為不可動的基石
- 不把 Super8、Cresclab 當作只讀靈感來源，最後靠直覺重新發明一遍

### 1.6.2 我們要做的事

- 用新的架構實作 Kachu+
- 但每一個核心模組都要能對應到四個專案中的已驗證來源
- 在產品、資料、流程、業務規則、執行模型五個層級都建立「來源對照」

一句話總結：

> **Kachu+ 是新產品，不是新孤島。**

---

## 2. 目標使用者（ICP）

**主要 ICP：預約型服務業個人商家**

- 美容師、美甲師、美體師
- 補教業者（個人家教、小型補習班）
- 健身教練（個人工作室）
- 小型診所（中醫、牙醫、物理治療）
- 個人攝影師、婚紗工作室

**共同特徵：**

- 1–5 人的小團隊或個人工作者
- 沒有專職行銷或客服角色，老闆自己兼任一切
- 顧客回頭率是生意命脈，但沒有系統管理顧客
- 現在用 LINE 跟顧客溝通，但管理方式混亂（手動貼紙、記憶、人工追蹤）
- 對「行銷工具」有距離感，但願意讓「助理」幫忙

**次要 ICP（v2 才進入）：**

- 餐飲業（外送平台之外想要直接顧客關係的店主）
- 電商小賣家（想要回購而不只是廣告）

---

## 3. 核心行為模式：Mixed-Initiative Interaction

這是 Kachu+ 區別所有現有工具的核心設計原則。

### 3.1 兩種模式並存

**被動模式（有指令就執行）**

品牌陣地類：
> 商家：「幫我把上週的 Google 評論都回一下」  
> Kachu+：「你上週有 5 則新評論，3 則五星我幫你草擬了感謝回覆，2 則三星以下我列出了處理建議，確認後一次送出？」

顧客關係類：
> 商家：「幫我通知下週要來的客人確認一下」  
> Kachu+：「你下週有 8 位客人，我幫你發確認訊息，要用哪個版本？A：正式版 / B：輕鬆版」

**主動模式（沒指令也觀察）**

品牌陣地類：
> Kachu+（週四早上）：「你的 Google 評分本週從 4.7 降到 4.5，有兩則負評沒有回覆超過 3 天了。要先處理嗎？」

顧客關係類：
> Kachu+（週一早上）：「上個月的老客人裡，有 12 位超過 60 天沒來了。上次你對這類客人發『好久不見』優惠，有 4 位回來。這次要試試嗎？」

### 3.2 三層確認原則

Kachu+ 永遠不在未確認的情況下對顧客發訊息。  
每個主動行動的路徑是：**觀察 → 建議（含理由）→ 商家確認 → 執行 → 回報結果**

這不是技術限制，是產品信任的核心。商家要知道 Kachu+ 在做什麼，才敢把顧客關係交給它。

---

## 4. V1 切入場景：雙迴圈同步啟動

### 4.1 v1 的策略選擇

Kachu v2 已驗證品牌陣地管理的產品價值（Google/FB/IG 發文、回覆、報表）。  
Kachu+ v1 的任務是：**在新架構上同時建好品牌陣地與顧客關係兩層，讓飛輪第一次轉動。**

v1 的優先順序是：
1. 品牌陣地管理（沿用已驗證工作流，greenfield 重建，優先完成確保 Day 1 可用）
2. 顧客關係管理（v1 的核心新建目標，建立後飛輪才開始轉）

### 4.2 第一個 aha moment：兩個場景都要有

**品牌陣地 aha moment（v1 最高優先，確保 Day 1 可用）：**
> 「Kachu+ 幫我把上週積欠的 7 則 Google 評論全部回覆了，其中那則投訴也幫我起草了誠懇的道歉，我只改了兩個字就送出。」

**顧客關係 aha moment（需要建立名單後才能觸發）：**
> 「Kachu+ 找到了 12 位超過 60 天沒來的老客人，幫我發訊息後，有 4 位回來預約了。」

### 4.3 V1 的完整使用者旅程

```
商家第一次設定 Kachu+
│
├── Phase 1：建立品牌陣地層（greenfield 重建，工作流參考 Kachu v2 已驗證設計）
│    ├── 連接 LINE OA（指揮介面）
│    ├── 連接 Google Business Profile
│    ├── 連接 FB / IG（選填）
│    └── 第一個任務：「幫我看看有什麼需要處理的」
│         └── Kachu+ 掃描後回報：「Google 有 3 則未回評論、IG 上週互動率下滑」
│
├── Phase 2：建立顧客記憶（新建）
│    ├── 對話引導：「你的生意是什麼？客人多久來一次算正常？」
│    │    └── Kachu+ 學習「這家的回訪週期基準」
│    └── 匯入第一批顧客（LINE 好友 / 手動輸入 / CSV）
│         └── 每個顧客建立基本 profile：LINE ID + 最後互動時間
│
└── Phase 3：主動建議開始運作
     ├── 品牌陣地建議：「你的 Google 評分下滑，要處理嗎？」
     ├── 顧客關係建議：「有 12 位老客人超過 N 天沒來，要發個訊息試試嗎？」
     └── 兩類建議都走同一個確認流程 → 執行 → 回報結果
```

### 4.4 V1 的邊界

**V1 包含：**
- 品牌陣地：Google/FB/IG 發文、評論回覆、洞察摘要（greenfield 重建，沿用 Kachu v2 驗證的工作流定義）
- 顧客記憶：profile 建立、標籤、最後互動時間、沉睡偵測
- 主動建議：品牌陣地類 + 顧客關係類，統一確認流程
- 訊息發送 + 回應追蹤
- 簡單結果回報

**V1 不包含：**
- 複雜分眾規則（v2）
- 自動旅程（v2）
- 優惠券整合（v2）
- 報表 dashboard（v2）
- 第二個渠道（WhatsApp/SMS，v2）

---

## 5. 顧客記憶層（Customer Memory Layer）

這是 Kachu+ 最重要的新建部分，也是三個參考專案都沒有完整解決的問題。

### 5.1 設計原則（借鑑 OneLoop Core）

以 Cresclab OneLoop 的 unified profile + channel entity 模型為設計前例，但針對 SMB 場景大幅簡化：

```
CustomerProfile
├── identity
│    ├── kachu_contact_id（UUID，平台主鍵）
│    ├── display_name（商家目前最常用的稱呼）
│    └── custom_name（商家自訂名稱，例如「三號固定客阿芳」）
│
├── channel_entities[]
│    ├── channel_type（v1 只有 LINE）
│    ├── external_user_id（例如 LINE user_id）
│    ├── reachability_status
│    └── link_confidence
│
├── memory
│    ├── last_interaction_at（最後互動時間）
│    ├── interaction_count（互動次數）
│    ├── tags[]（商家貼的標籤，例如「常客」「過敏體質」「不喜歡聊天」）
│    ├── notes（商家手寫備註，自由文字）
│    └── ai_observations（AI 從對話記錄推導的觀察，需商家確認才寫入）
│
├── lifecycle
│    ├── first_seen_at
│    ├── is_sleeping（bool，超過 threshold 沒互動）
│    ├── sleep_since_days（睡眠天數）
│    └── status: active / sleeping / churned / blacklisted
│
└── consent
     ├── opt_out（退訂，不可發送主動訊息）
     └── source（如何加入：LINE 互動 / 商家匯入 / 名片掃描）
```

### 5.2 冷啟動策略

大多數商家第一天沒有任何數位名單。三個路線並存：

**路線 A（最快）：LINE Official Account 好友匯入**  
直接從既有 LINE OA 取得好友清單，自動建立 profile。問題是「沒有互動歷史」，只有好友關係。

**路線 B（最準）：預約系統匯入**  
商家提供現有預約記錄（CSV / 手動填表），以手機號或姓名做第一次身份建立。後續等 LINE 訊息進來時做身份配對。

**路線 C（最輕）：對話引導商家輸入**  
Kachu+ 問商家：「你上週有哪幾位常客？他們大概幾號會來？」靠商家的知識建立初始記憶。驗證商家是否願意用自然語言描述生意，是 v1 最重要的假設驗證。

**v1 優先路線：C → A → B**（從最輕量的行為驗證開始）

### 5.3 業務規則（借鑑 Super8 客戶中心規則）

直接採用 Super8 的規則，不再重新設計：

- R1：同一 LINE user_id 在同一 tenant 內只能映射到一個 active profile
- R2：profile merge 不得遺失歷史互動記錄與標籤
- R3：退訂或黑名單的顧客不得進入任何發送名單
- R4：商家刪除標籤時，歷史互動紀錄不受影響
- R5：AI 觀察（ai_observations）在寫入正式 profile 前需商家確認

---

## 6. 主動建議引擎（Proactive Nudge Engine）

這是 Kachu+ 的核心差異化能力，三個參考專案都沒有對應的 SMB-first 設計。

### 6.1 觸發邏輯

建議分為兩大類，由同一個排程引擎產生，走同一套確認流程。

```
每日排程（商家上線時間內）：
│
├── 【品牌陣地類建議】
│    ├── 掃描未回覆評論（超過 48h 未回）
│    │    └── 產生「有 N 則評論等你回覆」建議
│    ├── 掃描評分變化（近 7 天平均評分下滑 > 0.2）
│    │    └── 產生「你的 Google 評分下滑了」建議
│    ├── 掃描最後發文時間（超過 N 天未在 GBP/FB/IG 發文）
│    │    └── 產生「已經 X 天沒發新內容了，要發一篇嗎？」建議
│    └── 掃描 IG/FB 互動率異常（比上個月同期低 30%）
│         └── 產生「最近互動率下滑，上次這類貼文表現最好」建議
│
├── 【顧客關係類建議】
│    ├── 掃描 sleeping profiles（超過 threshold 天數）
│    │    └── 若 sleeping count > 0 → 產生「找回老客人」建議
│    ├── 掃描近期互動量下滑（過去 7 天比 30 天日均低 30%）
│    │    └── 產生「最近來的客人比較少，要試試什麼嗎？」建議
│    └── 掃描即將到期的週期型服務（如果有預約資料）
│         └── 若 N 天後應該回來的客人還沒預約 → 產生「該提醒他了」建議
│
└── 商家主動觸發（走 intent_router 的 CONSULT 模式）
     └── 業務顧問對話（沿用 Kachu v2 驗證的互動模式，新架構重建）
```

### 6.2 建議卡設計

每一個建議必須包含：

```
{
  suggestion_type: "recover_sleeping" | "prevent_churn" | "rebooking_nudge" |  // 顧客關係類
                   "unanswered_reviews" | "rating_drop" | "content_gap" | "engagement_drop" | ...,  // 品牌陣地類
  category: "customer_relationship" | "brand_presence",  // 分類，供 UI 分群顯示
  title: "有 12 位老客人超過 60 天沒來了",
  reason: "上個月對這類客人發訊息，4 位回來預約",
  affected_profiles: [list of profile IDs],
  profile_count: 12,
  suggested_action: "發一封『好久不見』訊息",
  draft_message: "（可直接使用或修改的訊息草稿）",
  expires_at: "（如果不處理，多久後這個建議就失效）",
  status: "pending" | "accepted" | "dismissed" | "sent"
}
```

### 6.3 確認流程（沿用 approval-first 原則，於新架構重建）

```
Kachu+ 建議 → 商家 LINE 收到通知
│
├── 商家回「好」或點確認 → 進入訊息草稿確認
│    ├── 商家回「就這樣」→ 發送
│    ├── 商家修改後確認 → 發送
│    └── 商家說「不用了」→ 標記 dismissed
│
└── 商家忽略 → N 小時後提醒一次（僅提醒一次）
     └── 仍無回應 → 自動 expire，不再騷擾
```

---

## 7. 技術架構（greenfield 重建，系統性借力）

### 7.0 架構原則：四層地基，每層有一個不可妥協的驗證教訓

Kachu+ 的架構論點不是「從四個專案各借一點」，而是：**系統的每一層都必須站在已被現實驗證的地基上**。四個專案各自在不同層次留下了教訓——有些是正面驗證（這個做法真的有效），有些是負面驗證（這個假設是錯的）。

| 層次 | 核心教訓 | 驗證來源 | Kachu+ 的對應原則 |
|---|---|---|---|
| **UX 層** | 商家不需要 dashboard，對話就夠了 | Kachu v2：135 production 驗證項目，LINE 指揮介面真實可運作 | 不建後台，LINE 是唯一操作介面；ContextBriefManager 的設計直接採用 |
| **規則層** | 業務規則不是建議，是防止真實損失的安全網 | Super8：每一條規則背後對應一個真實失誤（silent bug / 資料遺失 / 法規風險）| 直接採用，不重新推導；違反任一條都對應可量化的商家信任損失 |
| **資料層** | Profile 模型設計錯了，後面所有功能都歪 | Cresclab：unified_profile / channel_entity / profile_link 三層分離，每個 merge 決策都有 confidence_score 和 audit log | v1 只有 LINE，但 identity model 從第一天就設計為可擴展；line_user_id ≠ customer |
| **執行層** | Durable execution 不能只有口頭抽象；approval gate 需要 DB-backed 狀態機 | AgentOS：Task + Plan + Steps + approval lifecycle 全部持久化到 PostgreSQL；idempotency_key 防重複；side_effect_level 決定是否觸發人工確認；明確 defer 了 Temporal（v1 足夠，v2 評估）| 直接接 AgentOS 的 approval gate；每日 nudge scan 作為 task 建立，process 重啟可 resume |

**四個層次的依賴關係不是平行的：**

```
執行層正確 → 業務規則才能在邊界條件下被可靠執行
資料層正確 → 規則才有正確的對象可操作（merge 不會遺失資料）
規則層完整 → UX 層的確認流程才有安全保護
UX 層成立  → 所有層次的設計才有商業意義
```

底層設計錯了，上層功能越強越危險。這是 Kachu+ 不做快速 prototype 的原因——不是因為謹慎，而是因為底層的設計決策在後期修正的代價是線性增長的。

### 7.1 可直接移植的已驗證模組（以 Kachu v2 為主）

以下是深度 code review 後確認可直接移植到 Kachu+ 的模組。這些不是「設計靈感」——是可以直接讀程式碼、按相同邏輯在新架構實作的工作成果。

---

**① IndustryPlaybook（`industry_playbook.py`）— 直接移植**

四個行業 profile（beauty/restaurant/cafe/retail），每個包含：
- `tone`：寫作語氣定義
- `content_angles`：適合這行業的貼文角度
- `consultant_focus`：業務顧問對話的重點方向
- `customer_motivations`：這行業顧客的決策心理
- `market_watchpoints`：需要定期監測的指標

還有月份市場事件日曆（12 個月的節慶主題、行銷重點），這些是 Kachu+ 內容建議引擎的背景知識，直接用。

**Kachu+ 的對應**：LLM 生成貼文/回覆草稿時，注入 `build_industry_context(tenant.industry_type)` 的結果作為 system context 的一部分。

---

**② KachuExecutionPolicyResolver（`policy.py`）— 直接移植**

根據商家歷史行為（approval rate、edit_delta）動態調整 approval timeout：
- 高信任商家（acceptance > 85%，edit 幅度小）→ timeout 縮短至 6 小時
- 低信任商家（acceptance < 50%）→ 加入 `require_direction_check` 提示，讓 LLM 草稿更保守

這是 per-tenant personalization 的真實機制，不是口號。直接用在 Kachu+ 的 suggestion card approval 流程。

---

**③ OnboardingFlow bot messages + 3-question interview（`onboarding/flow.py`）— 設計直接繼承**

5 步驟引導：店名 → 行業 → 地址 → 文件上傳 → 3 題訪談（核心差異 / 最大困擾 / 今年目標）。

Kachu+ 的冷啟動路線 C（對話引導商家輸入）可以直接繼承這個流程結構，只需要在 Step 2 增加顧客管理相關問題（「你的客人大概多久來一次算正常？」）。

---

**④ ApprovalBridge（`approval_bridge.py`）— 設計直接繼承**

LINE postback（approve / reject / edit）→ AgentOS approval decision 的完整橋接邏輯：
- EDIT 動作開啟 edit session，等商家改完再送出
- APPROVE 後若 AgentOS 還有 system-level checkpoint，自動 auto-approve 讓工作流繼續
- `edited_payload` 把商家改過的草稿帶回 AgentOS，確保發出去的是商家版本

Kachu+ 的所有對外操作都需要這個 bridge。設計繼承，在新架構中重建。

---

**⑤ PostTaskReviewService（`post_task_review.py`）— 設計直接繼承**

每次工作流完成後的 learning loop：
- `after_preference_update`：記錄 original↔edited diff（內部呼叫 `store_preference()` + `_compute_diff_notes()`），更新 Layer 3 偏好記憶
- `after_approval_decision`：記錄 outcome，更新 Layer 4 episode 記憶
- 然後非同步 `refresh_briefs`，讓下一次 LLM call 注入更新後的 context

**這是 §7.6 所說「編輯即訓練」的實際程式碼**。Kachu+ 的 learning loop 按相同邏輯實作。

---

**⑥ ProactiveMonitorAgent 的偵測邏輯（`proactive_monitor.py`）— 直接移植 3 種偵測條件**

```python
NUDGE_NO_POST = "no_recent_post"          # 7天無發文 → 提醒發文
NUDGE_NEGATIVE_REVIEW = "pending_negative_review"  # 1小時內有未回負評 → 提醒回覆
NUDGE_STALE_KNOWLEDGE = "stale_knowledge_base"     # 60天知識庫未更新 → 提醒補充
```

Kachu+ 的 nudge engine 在這三種之上增加顧客關係類（沉睡偵測），但觸發邏輯的框架直接繼承。

---

**⑦ AgentOS 的 workflow pipeline 結構（`kachu_workflows/*.py`）— 直接用作模板**

`review_reply_pipeline.py` 和 `google_post_pipeline.py` 已定義完整的 Plan + Steps，有 `confidence`、`side_effect_level`、`timeout_seconds`、`approval_timeout`。

Kachu+ 所有對外工作流（評論回覆 / 發文 / 喚醒訊息發送）都按相同結構定義，不需要重新設計格式。

---

### 7.2 需要新建的部分（Kachu v2 沒有，三個專案都沒有直接前例）

| 能力 | 說明 | 參考設計 |
|---|---|---|
| CustomerProfile service | Kachu v2 的 `crm_enabled` flag 從未實作，顧客記憶層需要完全新建 | Cresclab `unified_profiles` schema 的三層設計（profile / channel_entity / link） |
| Sleep detection 排程 | 每日掃描 `last_interaction_at`，計算 `sleep_since_days`，更新 profile status | Super8 自動旅程 R3（delay step 前重檢顧客狀態）的觸發邏輯 |
| Proactive nudge engine（顧客關係類）| 沉睡偵測 + 建議卡產生，這是 Kachu+ 差異化的核心 | 全新，無現成前例 |
| customer_brief（第三份 Context Brief）| 每次生成顧客訊息時，注入「這類客人的歷史回應模式」| 延伸 Kachu v2 的 ContextBriefManager 三份 brief 架構 |
| Suggestion card 完整狀態機 | `pending → accepted/dismissed → sent → expired → reported` | Super8 群發 R3 的 campaign lifecycle + AgentOS task status |
| Result tracking | 建議發送後 7 天的回應率追蹤 | Cresclab EPIC-E7 的 delivery feedback loop 設計思路 |

### 7.3 AgentOS 的定位：正式 Execution Runtime，直接接入

AgentOS（`github.com/joyshotapp/agentos`）是真實可用的 execution runtime，不是口頭抽象。Kachu+ 直接使用它，不重新造輪子。

**AgentOS 為 Kachu+ 解決的核心問題**：

| Kachu+ 需求 | AgentOS 的解法 |
|---|---|
| Suggestion card 生命週期管理 | Task + approval lifecycle（pending → approved/rejected → completed）|
| 每日 nudge scan 不因 process crash 消失 | Task 狀態持久化到 PostgreSQL，重啟後從 `current_run_id` resume |
| 對外發送前強制商家確認 | `side_effect_level = IRREVERSIBLE_WRITE` 自動觸發 approval gate |
| 同一事件不重複執行 | `idempotency_key` 在 task 層防止重複建立 |
| 失敗追蹤 | TraceRecorder + tool_call 記錄，每個步驟結果可查 |

**已知限制（bootstrap-roadmap 明確 defer 的）**：
- 沒有 Temporal 的 event sourcing——process crash 後 in-flight task 需要外部機制重新 trigger（不是自動 retry）
- 長期記憶推送（memory promotion）是 Priority 3，尚未實作
- Tenant-specific model policy defer

對 Kachu+ v1 來說，這個等級已足夠。v2 才評估是否需要 Temporal 處理更複雜的 journey 場景。

### 7.4 資料層核心 tables

在 Kachu+ 的新資料模型中，至少需要以下核心 tables。注意：`line_user_id` 不是 customer 本體欄位，而是渠道身份的一種；v1 雖然只有 LINE，也要從第一天保留三層 identity 結構，避免之後加渠道時痛苦 migration。

```sql
-- 顧客主檔
kachu_customer_profiles (
  id UUID PK,
  tenant_id UUID FK,
  display_name TEXT,
  custom_name TEXT,
  status TEXT, -- active / sleeping / churned / blacklisted
  last_interaction_at TIMESTAMPTZ,
  interaction_count INTEGER,
  sleep_since_days INTEGER,
  opt_out BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
)

-- 渠道身份（v1 只有 LINE，但表先建好）
kachu_channel_entities (
  id UUID PK,
  tenant_id UUID FK,
  channel_type TEXT, -- line / whatsapp / sms / booking_system
  external_user_id TEXT,
  reachability_status TEXT, -- reachable / opted_out / blocked / unknown
  occurred_at TIMESTAMPTZ,
  received_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ,
  UNIQUE (tenant_id, channel_type, external_user_id)
)

-- profile 與渠道身份的連結
kachu_profile_links (
  id UUID PK,
  tenant_id UUID FK,
  profile_id UUID FK,
  channel_entity_id UUID FK,
  confidence_score NUMERIC(5,4),
  resolution_source TEXT, -- imported / manual / inferred
  resolution_note TEXT,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ,
  UNIQUE (tenant_id, channel_entity_id)
)

-- 標籤
kachu_customer_tags (
  id UUID PK,
  tenant_id UUID FK,
  profile_id UUID FK,
  label TEXT,
  source TEXT, -- manual / ai_observation
  confirmed_at TIMESTAMPTZ, -- ai_observation 需要此欄
  created_by TEXT,
  created_at TIMESTAMPTZ
)

-- 主動建議卡
kachu_suggestions (
  id UUID PK,
  tenant_id UUID FK,
  suggestion_type TEXT,
  title TEXT,
  reason TEXT,
  affected_profile_ids UUID[],
  profile_count INTEGER,
  suggested_action TEXT,
  draft_message TEXT,
  status TEXT, -- pending / accepted / dismissed / sent / expired / reported
  expires_at TIMESTAMPTZ,
  sent_at TIMESTAMPTZ,
  result_snapshot JSONB,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
)
```

---

### 7.5 V1 工程切片計畫

> 這一章回答的問題：v1 先做哪五個模組、每個模組具體看哪個檔案、哪些程式碼幾乎可以直接用、哪些要新建、做完長什麼樣。

---

#### 模組一：Onboarding + LINE 指揮介面

**任務**：商家第一次設定 Kachu+（冷啟動引導），以及日常透過 LINE 下指令。

**直接移植的程式碼**：
- `Kachu_v2/src/kachu/onboarding/flow.py` — `_BOT_MESSAGES` dict 和 5-step 流程結構直接繼承。在 Step 2（詢問行業）之後增加：「你的客人通常幾天來一次算正常？」建立 `sleep_threshold` 初始值。
- `Kachu_v2/src/kachu/intent_router.py` — `BossRouteMode` enum（EXECUTE / CONSULT / CLARIFY）和分類邏輯。新架構中重建 router，但分類模式不變。
- `Kachu_v2/src/kachu/line/webhook.py` — multi-tenant webhook 接收、tenant 識別、signature 驗證的設計邏輯。

**需要新建**：router 本身（呼叫路徑換成新架構）；顧客記憶類 intent（「幫我看看有哪些客人沉睡了」）。

**完成定義**：商家可以完成 5-step onboarding；日常 LINE 指令被正確分類為 EXECUTE（執行型）/ CONSULT（諮詢型）/ CLARIFY（需澄清），並返回適當的回應。

---

#### 模組二：品牌陣地連接器 + 內容生成

**任務**：讀取 Google 評論/評分、生成回覆草稿、推播給商家確認、發布到 GBP；讀取 FB/IG 互動數據。

**直接移植的程式碼**：
- `AgentOS_real/src/agent_platform/kachu_workflows/review_reply_pipeline.py` — 完整的 Plan + Steps 定義（fetch-review → analyze-sentiment → retrieve-context → generate-reply → notify-approval → confirm → publish），approval_timeout 6h。**直接用這個格式定義 Kachu+ 的評論回覆工作流。**
- `AgentOS_real/src/agent_platform/kachu_workflows/google_post_pipeline.py` — 發文工作流（determine-post-type → retrieve-context → generate → notify-approval → confirm → publish），approval_timeout 48h。
- `Kachu_v2/src/kachu/industry_playbook.py` — `_INDUSTRY_PROFILES` dict（beauty/restaurant/cafe/retail）和 `_MONTHLY_MARKET_EVENTS`（12 個月市場事件日曆）。**直接 import，注入 LLM prompt 作為內容生成背景知識。**
- `Kachu_v2/src/kachu/approval_bridge.py` — LINE postback → AgentOS approval 的橋接邏輯（approve/reject/edit，含 auto-approve system checkpoint）。**設計直接繼承，在新架構中重建。**

**需要新建**：adapter interface 規格（統一邊界）；credential 管理與過期處理（參考 Super8 渠道 R5：credential 過期時所有下游能力降級）。

**完成定義**：能讀取 Google 評論清單、生成草稿推送商家、商家確認後自動發布；FB/IG 近期互動數據可讀取；所有失敗情境有明確回報路徑。

---

#### 模組三：顧客記憶層

**任務**：建立 CustomerProfile、打標籤、每日計算沉睡天數、讓商家查詢「沉睡客人列表」。

**資料設計參考**（看這些但不直接 copy schema）：
- `Cresclab/product_design/04_core_schema_spec.md` 的 `unified_profiles / channel_entities / profile_links` 三層——v1 只有 LINE，但 identity model 按三層設計，避免之後加渠道需要 migration。
- `unified_profiles.reachability_json`：用 JSONB 紀錄各渠道的可觸及狀態，SMB 版簡化後也要有這個概念。

**直接採用的規則**（Super8 客戶中心業務規則，不重新推導）：
- R2：同 LINE user_id 在同 tenant 只能映射一個 active profile
- R3：merge 不得遺失 timeline、tag、identity
- R6：黑名單 / 退訂不得進入可發送名單
- R8：刪除 tag 不破壞歷史 timeline

**完成定義**：能建立 profile 並關聯 LINE channel entity；商家能手動貼標籤；系統每日排程計算 `sleep_since_days`；商家在 LINE 問「哪些客人超過 60 天沒來？」能得到正確的列表回應。

---

#### 模組四：主動建議引擎

**任務**：每日排程掃描 → 產生建議卡 → 透過 LINE 推送 → 商家確認 → 執行 → 回報結果。

**直接移植的程式碼**：
- `Kachu_v2/src/kachu/proactive_monitor.py` — 三種偵測邏輯（7天無發文 / 1小時內有未回負評 / 60天知識庫未更新）。**v1 保留「未回覆評論」這條，加入「沉睡顧客偵測」作為第四條。**
- `Kachu_v2/src/kachu/policy.py` — `KachuExecutionPolicyResolver`：per-tenant 動態 approval timeout（高信任縮至 6h，低信任加 direction check）。**直接移植，讓每個商家的建議確認流程隨其使用習慣自動調整。**
- `Kachu_v2/src/kachu/post_task_review.py` — `after_preference_update` 的 learning loop（每次商家修改草稿後，記錄 diff，非同步 refresh briefs）。**這是「建議越來越準」的核心機制，設計直接繼承。**

**需要新建**：
- 排程器換成 durable job queue（不用 APScheduler，改用能在 process 重啟後繼續執行的方案）
- 建議卡 schema（`kachu_suggestions` table）
- 沉睡偵測邏輯（按 `last_interaction_at` + tenant 的 `sleep_threshold` 計算）

**執行邊界要分清楚**：durable job queue 只負責「每天什麼時候重新掃描一次」這種 recurring trigger；真正的建議執行、approval、resume、trace 仍交給 AgentOS。不要把 scheduler 和 execution runtime 混成同一層。

**直接採用的規則**（Super8 群發規則）：
- R1：建議送出前先 materialize audience snapshot（確認哪些 profile 是沉睡狀態，snapshot 後不再變動）
- R3：建議一旦進入 sending，不可修改 audience 與訊息內容

**完成定義**：系統每日自動掃描兩類場景（沉睡顧客 / 未回評論），觸發時產生建議卡並推送 LINE；商家能確認或拒絕；執行後 7 天回應率可查。

---

#### 模組五：學習閉環（Learning Loop）

**任務**：每次商家互動（修改草稿、接受/拒絕建議）都更新 per-tenant context，讓下一次 LLM call 更準。

**直接移植的程式碼**：
- `Kachu_v2/src/kachu/memory/manager.py` — 四層記憶架構（raw / structured / preference / episode）完整繼承，新增 Layer 5 顧客互動結果。
- `Kachu_v2/src/kachu/context_brief_manager.py` — `ContextBriefManager.refresh_briefs()` 的架構（TTL 30 天，非同步 refresh）直接繼承，擴充為三份 brief（brand / owner / customer）。
- `Kachu_v2/src/kachu/post_task_review.py` — 整個 `PostTaskReviewService` 設計直接繼承。每次工作流完成後：① 記錄 preference diff，② 記錄 episode，③ 非同步 refresh briefs。

**Layer 3 Preference 的關鍵設計（來自 Kachu v2，直接用）**：
商家每次修改草稿，系統記錄 `original↔edited` diff。下次生成時注入最近 3 筆改法作為 few-shot 範例。確認步驟不只是安全閘——它是風格學習的資料採集機制。

**完成定義**：商家修改草稿後，系統自動記錄修改方向；下次生成同類內容時，風格明顯更貼近商家。30 天使用後，建議接受率應高於前 7 天（可量化驗證）。

---

#### V1 明確不做（延後至 v2）

| 功能 | 原因 |
|---|---|
| 複雜分眾條件 | v1 名單量不足，先驗證基本沉睡偵測 |
| 自動旅程（trigger → delay → action 序列）| 依賴分眾成熟；Super8 自動旅程業務規則可直接借用，等 v2 |
| 報表 Dashboard | v1 用 LINE 文字回報就夠，先驗證商家願意互動 |
| 優惠券整合 | 超出 v1 範圍 |
| 第二渠道（WhatsApp / SMS）| 驗證完 LINE 場景再擴展 |
| 多用戶 RBAC | v1 ICP 是個人商家，單人操作 |
| AI 觀察自動寫入 profile | v1 只做手動標籤，AI 觀察需商家確認才寫 |

---

### 7.6 LLM 架構設計

#### 7.6.1 LLM 出現的四個觸點

LLM 在 Kachu+ 中是「語言介面 + 內容生成」，核心業務邏輯（誰沉睡、評分有沒有下滑）是規則引擎，不是 LLM。

| 觸點 | 輸入 | 輸出 | 要求 | 模型選擇 |
|---|---|---|---|---|
| **指令理解** | 商家 LINE 訊息 | intent 分類（EXECUTE / CONSULT）+ 參數抽取 | 快 > 準，延遲要低 | 小模型（Haiku / GPT-4o-mini）|
| **內容草稿生成** | 評論/發文需求 + brand_brief + owner_brief | 回覆草稿 / 貼文草稿 | 品質 > 速度，要像商家本人寫的 | 中模型（Sonnet / GPT-4o）|
| **業務顧問對話** | 商家問題 + 完整 context（GA4 / 沉睡率 / 評分趨勢）| 診斷 + 具體建議 | 品質最高，商家願意為此付費 | 最強可用模型 |
| **背景觀察推導**（v2）| 顧客對話歷史 | ai_observations（待商家確認）| 成本優先，可非同步批次 | 批次 / 非同步 |

**什麼不用 LLM：** 沉睡偵測（SQL）、建議卡觸發（規則引擎）、評分下滑計算（數值比較）、建議卡狀態機（pure logic）。這些用 LLM 是成本浪費，也會引入不必要的不確定性。

#### 7.6.2 五層記憶架構（繼承 Kachu v2，新增顧客層）

Kachu v2 已驗證的四層記憶設計（`memory/manager.py`）直接繼承，並新增第五層：

```
Layer 1 – 原始對話記錄（Raw Conversation Log）
  → 每次商家 LINE 訊息與系統回應都留存
  → 不直接注入 LLM，作為摘要的原料

Layer 2 – 品牌結構化知識（Structured Brand Knowledge）
  → 商家主動告知的事實：商品、促銷、聯絡方式、限制條件
  → 每筆做向量化（embedding），支援語意搜尋
  → 來源：商家上傳文件、對話中主動描述

Layer 3 – 商家偏好記錄（Preference / Edit Diffs）
  → 商家修改草稿時，系統自動記錄「original ↔ edited」的 diff
  → 下次生成時注入最近 3 筆改法作為 few-shot 範例
  → 關鍵設計：「編輯即訓練」——確認步驟不只是安全閘，也是風格學習的資料採集

Layer 4 – 工作流結果記錄（Episodic Memory）
  → 每次建議被接受、拒絕、或修改都記錄
  → 讓系統知道「這家商家通常接受哪類建議、拒絕哪類」
  → 以 category='episode' 存入 KnowledgeEntry（沿用 Kachu v2 設計）

Layer 5 – 顧客互動結果（Customer Interaction Outcomes）[Kachu+ 新增]
  → Kachu v2 的四層只覆蓋「商家側知識」，缺少「顧客側知識」
  → 記錄：哪類訊息這家的客人回應率高、哪個時段客人比較會看、哪位客人偏好不被主動聯繫
  → 讓顧客關係建議越來越精準，而不只是泛用模板
```

#### 7.6.3 三份 Context Brief

每次 LLM call 前，系統注入預先組合好的 brief，而非每次做即時向量搜尋（Kachu v2 驗證此設計：TTL 30 天，工作流完成後非同步 refresh）：

```
brand_brief（繼承 Kachu v2）
  ← Layer 2 品牌知識 + industry_context + Layer 4 episode 摘要
  → 「這家是什麼店、主要賣什麼、有什麼限制條件」
  → 所有 LLM call 都注入

owner_brief（繼承 Kachu v2，擴充建議接受模式）
  ← Layer 3 偏好範例（近 3 筆）+ 近期對話摘要 + Layer 4 episode
  → 「這家老闆怎麼說話、上次拒絕了什麼類型的建議」
  → 內容生成類 LLM call 注入

customer_brief（Kachu+ 新增）
  ← Layer 5 顧客互動結果，按「本次建議對象」動態組合
  → 「上次這家對沉睡客人發的訊息，回應率是 42%，這類客人偏好非正式語氣」
  → 顧客關係類建議生成時注入
```

#### 7.6.4 Fallback 原則

LLM 會輸出品質不一的結果，需要一致的降級策略：

| 情況 | 處理方式 |
|---|---|
| LLM call 失敗 / 超時 | fallback 到預設模板，不中斷商家體驗 |
| 輸出品質不過關（太短、語言錯誤、包含違禁詞）| 品質閘 → 失敗則用模板，標記 fallback_used |
| Intent 分類信心不足 | 回問商家：「你是想要 A 還是 B？」不猜測 |
| 任何對外送出的內容 | 永遠先過商家確認，不允許 bypass 確認步驟 |

---

## 8. 護城河分析

**什麼讓 Kachu+ 在一年後難以被複製？**

不是功能，而是每個 tenant 的 **AI 學習歷程**。

```
Day 1：Kachu+ 不知道這家的生意節奏
Day 30：Kachu+ 知道這家的回訪週期、客人偏好、老闆溝通風格
Day 90：Kachu+ 知道什麼樣的訊息這家的客人會回應
Day 180：Kachu+ 可以預測下個月哪些客人有流失風險
```

後進的競爭者可以複製功能，但無法複製這家商家 180 天的互動資料與 AI 學習結果。  
這是時間的護城河，不是技術的護城河。

**護城河的前提**：商家的資料不能被帶走給競爭者用，也不能因為換方案而歸零。  
這是一個倫理承諾，也應該是一個法律承諾（商家的資料屬於商家，Kachu+ 只是代為管理）。

---

## 9. 競品對比

| | Kachu+ | Super8 InsightArk | MAAC/CAAC | LINE 官方工具 | 人工助理 |
|---|---|---|---|---|---|
| 目標對象 | SMB 個人商家（1-5人）| 中大型品牌 | 中大型品牌 | 任何 LINE OA | 任何 |
| 品牌陣地管理 | ✅ Google/FB/IG | ❌ | ❌ | ❌ | 依人而定 |
| 顧客關係管理 | ✅ LINE CRM + 分眾 | ✅ 完整 | ✅ 完整 | 部分 | 依人而定 |
| 使用方式 | 對話即可操作 | 需學習 UI | 需學習 UI | 需學習 UI | 無學習成本 |
| 主動性 | 主動建議 + 確認 | 被動執行 | 被動執行 | 被動執行 | 依人而定 |
| 顧客記憶 | per-tenant AI 學習 | 標籤 + 分群 | 標籤 + 分群 | 無 | 人腦記憶 |
| 兩個迴圈打通 | ✅ 唯一做到的 | ❌ | ❌ | ❌ | ❌ |
| 月費 | 低（SMB 可負擔） | 高 | 高 | 免費但有限 | 高（人力） |
| 護城河 | 雙迴圈飛輪 + per-tenant 學習 | 功能廣度 | 功能廣度 | 平台壟斷 | 個人關係 |

**Kachu+ 的差異化不是「比企業工具便宜」，而是「唯一把品牌陣地管理和顧客關係管理打通的 SMB 工具」。**

---

## 10. 北極星指標

**v1 的北極星：飛輪第一次轉動**

> **商家在 30 天內，同時體驗到品牌陣地建議和顧客關係建議各至少一次，且兩者都有帶來具體結果**

具體定義：
- 品牌陣地結果：至少一則 Google 評論透過 Kachu+ 回覆（或一篇貼文透過 Kachu+ 發出）
- 顧客關係結果：至少一位沉睡顧客因 Kachu+ 建議的訊息而回來互動或預約

這個指標代表飛輪第一次轉動——商家同時解決了「吸引新客」和「維繫老客」兩個問題。

目標：**v1 GA 後前 30 個 tenant 中，有超過 40% 在 30 天內達到此指標**

**次要指標（監控健康度）：**
- 建議卡 accept rate > 60%（商家信任 Kachu+ 的建議）
- 月留存率 > 70%（使用後覺得有價值）
- 口碑推薦佔新商家來源 > 20%（month 2 開始觀察）

---

## 11. 關鍵假設與驗證順序

| 假設 | 驗證方式 | 驗證時機 |
|---|---|---|
| 商家願意用自然語言描述生意（冷啟動路線 C 可行） | 對前 5 個 tenant 做 1:1 引導對話，記錄完成率 | 第一個 tenant 上線後 |
| 商家願意確認 AI 建議而不是手動做 | 觀察建議卡 accept rate | 前 30 天 |
| 找回老客人訊息的回應率高到讓商家覺得有價值 | 追蹤建議發送後 7 天的回應率 | 第一個建議批次後 |
| 商家在使用 1 個月後留存 | churn rate month 1 | 第 30 天 |
| 商家願意邀請其他商家使用（口碑傳播） | 口碑推薦佔新商家來源比例 | 第 60 天 |

---

## 12. 四層地基：驗證來源與實作指引

Kachu+ 的綜效不是「四個專案加總」。每個專案在不同層次驗證了一個核心主張，Kachu+ 的架構在這四層上同時正確，才能避免各自為政的天花板。

---

### 12.1 UX 層：對話即介面（Kachu v2 驗證）

**已被驗證的主張**：SMB 商家不需要學習 dashboard，只要能用 LINE 下指令，就能操作完整的工作流。

**真實驗證基礎**：
- 135 個 production 驗證項目，主要業務流程（發文、評論回覆、知識更新、nudge、審批）都通過
- `ContextBriefManager`：每次工作流後非同步 refresh `brand_brief` + `owner_brief`，TTL 30 天，所有 LLM call 注入 brief 而非每次即時向量搜尋——這個設計已在 production 穩定運作
- `Layer 3 Preference`：商家每次修改草稿，系統自動記錄 diff 作為 few-shot 範例——「編輯即訓練」這個機制已驗證可用

**Kachu+ 的採用方式**：
- ContextBriefManager 的架構直接繼承，擴充為三份 brief（§7.6.3）
- intent_router 的 BossRouteMode 概念直接繼承，router 本身在新架構重建
- IndustryPlaybook 直接移植（見下方），不重新設計
- PolicyResolver 邏輯直接繼承，實現 per-tenant 動態 approval timeout
- `BackgroundTasks` + 單進程 APScheduler **不繼承**（這是已知架構風險：process crash = 排程任務靜默消失）

**實作時必查的檔案，以及具體要看什麼**：

| 檔案 | 看什麼 | 直接得到什麼 |
|---|---|---|
| `Kachu_v2/src/kachu/intent_router.py` | `BossRouteMode` enum、分類 function | 指令分類的 prompt 模式與結構 |
| `Kachu_v2/src/kachu/context_brief_manager.py` | `refresh_briefs()` 方法、`_build_brand_brief()`（私有）| Brief 組合邏輯與 TTL 設計，直接繼承 |
| `Kachu_v2/src/kachu/memory/manager.py` | `store_preference()`（記錄 diff）、`get_preference_examples()`（取出最近 N 筆作為 few-shot）| 四層記憶的實作，Layer 3 偏好記錄與取用機制 |
| `Kachu_v2/src/kachu/industry_playbook.py` | `_INDUSTRY_PROFILES` dict（4個行業）、`_MONTHLY_MARKET_EVENTS`（12個月） | **直接 import 使用**：LLM prompt 的行業知識背景，不需要重新設計 |
| `Kachu_v2/src/kachu/policy.py` | `KachuExecutionPolicyResolver.resolve()`、`acceptance_rate` 閾值 | per-tenant 動態 approval timeout 的完整邏輯，直接繼承 |
| `Kachu_v2/src/kachu/approval_bridge.py` | `handle_postback()`（主入口）、`complete_edit_and_approve()`（EDIT 流程）、`defer_with_schedule()`（延後提醒）| LINE postback → AgentOS approval 的橋接邏輯，直接繼承 |
| `Kachu_v2/src/kachu/post_task_review.py` | `after_preference_update()`（草稿修改後）、`after_approval_decision()`（審批決策後）| learning loop 的觸發時機與 brief refresh 機制，直接繼承 |
| `Kachu_v2/src/kachu/onboarding/flow.py` | `_BOT_MESSAGES` dict、5-step 流程、`redo` 支援 | 冷啟動 bot 對話腳本，直接複用，在 Step 2 後加 sleep_threshold 問題 |
| `Kachu_v2/src/kachu/proactive_monitor.py` | `scan_tenant_and_nudge()`（per-tenant 掃描入口）、`_detect_nudge()`（集中偵測三種條件）| 三種 nudge 常數（`NUDGE_NO_POST` / `NUDGE_NEGATIVE_REVIEW` / `NUDGE_STALE_KNOWLEDGE`）與 bucket-based 去重，直接繼承 |
| `Kachu_v2/docs/20260506-technical-assessment.md` | 全部 | **必讀**：區分哪些是真正驗證的能力，哪些是技術債，避免踩坑 |

---

### 12.2 規則層：業務規則是安全網（Super8 驗證）

**已被驗證的主張**：業務規則不是「最佳實踐建議」，是防止真實損失的安全網——每一條規則背後對應一個具體的失誤場景。

**顧客管理規則（採自 Super8 客戶中心 + 群發 + 自動旅程）**：

| 規則 | 若違反，會發生什麼 |
|---|---|
| 客戶中心 R2：同 LINE user_id 在同 tenant 只能映射一個 active profile | 同一顧客被拆成兩個 profile，訊息歷史和標籤分裂，無法追蹤完整互動 |
| 客戶中心 R3：merge 不得遺失 timeline、tag、identity | merge 後歷史消失，商家對顧客的記憶歸零，信任永久損失 |
| 客戶中心 R6：黑名單 / 退訂不得進入可發送名單 | 違反 LINE 使用條款，可能導致帳號被封；更嚴重是發送給不想被聯繫的顧客 |
| 群發 R1：發送前必須 materialize audience snapshot | 發送中名單改變，實際收到訊息的人與商家預期不符 |
| 群發 R3：campaign 進入 sending 後不可修改 audience 和內容 | 部分顧客收到舊版本，部分收到新版本，無法回溯 |
| 自動旅程 R3：delay step 前需重新檢查顧客狀態 | 旅程進行中顧客已退訂，但 delay 完後仍被發送——zombie 旅程 |
| 自動旅程 R5：exit goal 命中後不得再執行後續 step | 顧客已達到目標（預約成功）卻還繼續收到催促訊息 |
| MessageHero R7：人工接手後 agent 不得在未解除鎖定時繼續自動回覆 | 人工客服和 AI 同時回覆同一位顧客，體驗崩潰，信任損失 |

**渠道串接規則（採自 Super8 渠道串接業務規則——LINE webhook 必須實作）**：

| 規則 | 若違反，會發生什麼 |
|---|---|
| R3：驗簽失敗不進下游（LINE 簽章驗證） | 偽造的 webhook event 被當真實訊息處理，可能觸發真實發送行為 |
| R4：Webhook event 去重（`idempotency_key`）| LINE 重送機制（LINE 本身可能重送同一 event），相同事件觸發兩次 action |
| R8：provider rate limit——本系統側節流 | 爆量時依賴對方 API 報錯才停，recovery 更難；本側節流讓錯誤在可控範圍發生 |

**Kachu+ 的採用方式**：v1 直接採用這些規則，不重新推導，不做例外。

**實作時必查的檔案**：
- [02_業務規則/客戶中心_業務規則.md](02_業務規則/客戶中心_業務規則.md)：profile、merge、segment、黑名單
- [02_業務規則/群發訊息_業務規則.md](02_業務規則/群發訊息_業務規則.md)：audience snapshot、rate limit、campaign 狀態機
- [02_業務規則/自動旅程_業務規則.md](02_業務規則/自動旅程_業務規則.md)：enrollment、delay 重檢、exit goal
- [02_業務規則/渠道串接_業務規則.md](02_業務規則/渠道串接_業務規則.md)：webhook 驗簽（R3）、去重（R4）、rate limit（R8）
- [02_業務規則/MessageHero_業務規則.md](02_業務規則/MessageHero_業務規則.md)：human handoff 鎖定、tool allowlist

---

### 12.3 資料層：Profile 模型正確性決定上限（Cresclab 驗證）

**已被驗證的主張**：把 `line_user_id = customer` 是一條死路——同一個人可能有多個 LINE 帳號、多個渠道，flat model 在後期無法聚合，只能痛苦 migration。

**Cresclab 的核心設計洞察**（來自 `04_core_schema_spec.md`）：

```
unified_profile（人的本體）
    ↑ profile_link（連結，帶 confidence_score + 決策 audit log）
channel_entity（人在某個渠道上的身份）
```

`profile_link` 上有 `confidence_score` 和 `unify_resolution_logs`——身份整合不是 binary 的事，每次 merge/link 決策都有不確定性，必須可追溯、可撤銷。

**Event envelope 設計**（來自 `06_core_event_contracts.md`，Kachu+ 必須採用的模式）：

每個互動事件帶兩個時間戳：
- `occurred_at`：事件真正發生的時間（例如顧客送訊息的時間）
- `received_at`：我們的系統收到這個事件的時間

兩個時間戳分開記錄是因為網路延遲、webhook 重送、provider 批次處理——「什麼時候發生」和「什麼時候我們知道」是不同的事，分析數據時必須能區分。

`idempotency_key` 在 event 層防重複（不同於 task 層的去重），確保同一個 LINE webhook event 重送時不會建立兩條 timeline 記錄。

**Kachu+ 的採用方式**：SMB 化簡化版——v1 只有 LINE，不需要 multi-workspace RBAC，但 identity model 從第一天就按三層設計，確保 v2 新增 WhatsApp / 預約系統 import 時不需要痛苦 migration。每個互動事件都帶 `occurred_at + received_at + idempotency_key`。

**實作時必查的檔案**：

| 檔案 | 看什麼 | 直接得到什麼 |
|---|---|---|
| `cresclab/product_design/04_core_schema_spec.md` | `unified_profiles / channel_entities / profile_links / unify_resolution_logs` 的完整 schema | Identity model 的設計，SMB 化時簡化 workspace 層即可，三層結構保留 |
| `cresclab/product_design/06_core_event_contracts.md` | Event envelope：`occurred_at` / `received_at` / `idempotency_key` | 所有 timeline 事件的欄位設計，直接採用這個 envelope 格式 |

---

### 12.4 執行層：AgentOS 是真實可用的 Execution Runtime（重新評估）

**前次評估的錯誤**：文件先前寫「AgentOS 是空殼，只有 stub tests」——這是基於本地 `/Desktop/AgentOS/` 目錄（確實只有一個 tests 目錄）的評估，不是完整 repo。完整 repo（`github.com/joyshotapp/agentos`）有真實 source code。

**AgentOS 真正有的能力**（來自 `src/agent_platform/`）：

```
WorkflowService（service.py）
├── create_task：Task + Plan + Steps，帶 idempotency_key 防重複建立
├── run_task：Resume 邏輯——若 task 已有 RUNNING/WAITING_APPROVAL run，
│            直接 expire 超時 approval 並 return，不重複執行
├── ApprovalPolicy：每個 Step 帶 side_effect_level（READONLY / REVERSIBLE_WRITE /
│                   IRREVERSIBLE_WRITE），policy 決定是否需要人工確認才能繼續
└── TraceRecorder：task creation / run start / approval wait / tool execution / completion
                   全部有 trace

資料模型（tables.py）
├── tasks：tenant_id + idempotency_key + status 狀態機
├── plans：每個 task 對應一個 Plan，內含有序 Steps
├── runs：每次執行是一個 run，有 parent_run_id（支援 replay）
├── approvals：決策 pending / approved / rejected / edited / timed_out
└── tool_calls：每次 tool 執行都有記錄，支援 SKIPPED_REPLAY
```

**AgentOS 為 Kachu+ 真正解決的問題**：

| 問題 | AgentOS 的解法 |
|---|---|
| Process crash 後任務靜默消失 | Task + Run 狀態持久化到 PostgreSQL；重啟後可從 `current_run_id` resume |
| 對外操作前需要商家確認 | `side_effect_level = IRREVERSIBLE_WRITE` 自動觸發 approval gate |
| 同一個 trigger 重複執行 | `idempotency_key` 在 task 層防止重複建立 |
| 無法追溯哪個步驟出了問題 | TraceRecorder + tool_call 記錄，每個 step 的執行結果都可查 |
| Approval 沒有回應怎麼辦 | `approval_timeout_seconds` + `timed_out` 決策，不會永久卡住 |

**需要正視的限制**（`bootstrap-roadmap.md` 明確 defer 的項目）：

- ⏸ **Temporal 整合**：明確 defer，目前沒有 Temporal 的 durable workflow 能力
- ⏸ **長期記憶推送**：memory promotion 是 Priority 3，尚未實作
- ⏸ **Tenant-specific model policy**：defer，模型路由未完成

這代表 AgentOS 目前是「**DB-backed approval + idempotency runtime**」，不是完整的 durable workflow engine（沒有 Temporal 那種 event sourcing + automatic retry on any failure）。但對 Kachu+ v1 的需求——每日 nudge scan、suggestion card 生命週期、approval gate——這個等級是足夠的。

**Kachu+ 的採用方式**：
- Suggestion card 的審批流程（pending → approved/rejected → sent）直接接 AgentOS 的 approval gate
- 每日 nudge scan 作為 task 建立，狀態持久化，process 重啟後可 resume
- `side_effect_level = IRREVERSIBLE_WRITE` 用於所有對外發送（LINE push / Google 發文 / 評論回覆）
- v2 才評估是否需要引入 Temporal 處理更複雜的 journey 場景

**實作時必查的檔案，以及具體要看什麼**：

| 檔案 | 看什麼 | 直接得到什麼 |
|---|---|---|
| `AgentOS_real/src/agent_platform/service.py` | `create_task()`（line 80）、`run_task()`（line 107）的 resume 邏輯（`current_run_id` 存在時直接 resume，lines 107-140）| Task + Run 狀態機的完整 code，直接複用這個模式設計 Kachu+ 的 suggestion card lifecycle |
| `AgentOS_real/src/agent_platform/models.py` | `TaskStatus`、`RunStatus`、`ApprovalDecision`、`SideEffectLevel` enum | 所有狀態定義，直接採用，不要自己重新定義 |
| `AgentOS_real/src/agent_platform/kachu_workflows/review_reply_pipeline.py` | `build_kachu_review_reply_plan()` function（回傳 Plan 物件）與其 Steps 定義（每個 Step 有 `confidence` / `side_effect_level` / `timeout_seconds` / `approval_timeout`）| **這是寫 Kachu+ workflow 的模板**：照相同格式定義「沉睡喚醒工作流」和「評論回覆工作流」 |
| `AgentOS_real/src/agent_platform/kachu_workflows/google_post_pipeline.py` | `build_kachu_google_post_plan()` function（5個 Steps：determine-post-type → retrieve-context → generate → notify-approval → confirm[IRREVERSIBLE] → publish[IRREVERSIBLE]）| 對外寫入類工作流的雙重確認設計，直接採用 |
| `AgentOS_real/src/agent_platform/contracts/capabilities.py` | `Capability` enum（8 個能力） | 理解 AgentOS 設計假設的能力邊界 |
| `AgentOS_real/alembic/versions/` | 全部 4 個 migration（特別是 `task_idempotency_and_approval_timeout`） | 理解 schema 演進路徑，避免重複踩過的坑 |
| `AgentOS_real/docs/planning/bootstrap-roadmap.md` | 全部 | **必讀**：哪些已完成、哪些明確 defer，避免依賴還沒有的功能 |

---

## 13. 開放問題（目前未決策）

1. **定價模型**：訂閱制（月費）vs. 用量制（按發送數）vs. 成效分潤（按找回的客人數）？  
   - 成效分潤最符合價值主張，但收費結算複雜

2. **人工客服的角色**：Kachu+ v1 只做主動行銷觸達，不做客服 inbox。但商家顧客來訊息時要怎麼辦？  
   - 目前 Kachu v2 有 FAQ 路由，但 FAQ send-or-escalate 是已知設計缺口

3. **LINE PUSH 訊息費用誰負擔**：LINE API 推播有費用，由商家自己申請 LINE OA 或 Kachu+ 代為管理？

4. **跨渠道時間點**：v2 第一個要加的渠道是 WhatsApp（東南亞市場）還是 SMS（覆蓋不用智慧型手機的顧客）？

5. **多商家協作**：有些美容師是在工作室租場次的，顧客屬於美容師個人還是工作室？這會影響 tenant 模型設計。

---

*本文件基於 Kachu_v2（early beta，305 tests passing）、Super8（14 模組完整業務規則）、Cresclab OneLoop（Phase 6 GA 完成）、AgentOS_real（真實 WorkflowService + approval lifecycle source code）四個專案的深度審閱後起草。所有借鑑來源均已在第 12 節列明。*
