# Kachu v2 第一年度租戶承載能力評估

日期：2026-05-06

## 一、評估目的

本文件評估的不是整體市場規模，也不是理想狀態下理論可支撐的總客戶數，而是：

在目前 Kachu v2 已完成的架構、部署方式、驗證結果、外部 API 限制與營運現況下，第一年是否足以支撐商業化早期導入，以及合理可承諾的租戶數區間為何。

本評估聚焦於：

- 現有 production 架構是否足以支撐第一年商用 beta / pilot
- 系統瓶頸會先出現在何處
- 應以多少活躍租戶作為第一年合理承載目標
- 哪些前提成立時可以較有把握擴到更高區間
- 哪些情況下不應對外承諾高租戶量

## 二、評估範圍與限制

本評估基於目前 repo、production 驗證結果與文件事實，不是假設未來完成所有理想重構後的能力上限。

### 納入評估的基礎

- 現行 production 為單台 Linux 主機 + Docker Compose + nginx + PostgreSQL + Redis + AgentOS + Kachu
- Kachu 主服務為單一 uvicorn process 啟動
- scheduler 在 Kachu app process 內啟動，非獨立 worker
- LINE webhook 後續處理主要依賴 FastAPI BackgroundTasks，而非 durable queue
- 多數 workflow 已在 production 驗證通過，尤其是 FB 發文、knowledge update、approval、nudge、audit、Meta insights 系列
- Google Business / GA4 相關能力仍受外部平台配額與准入限制，不應納入目前已成熟可大量供給能力

### 不納入本次評估的內容

- 全市場 TAM / SAM / SOM
- 假設未來完成分散式 queue、水平擴展、多 worker、真正 orchestration 重構後的容量
- 假設 Google / Meta / LINE / LLM 外部配額完全無限制的理想情況

## 三、系統現況摘要

### 1. 部署型態

目前 production 部署模式是單機 Docker Compose。相關依據如下：

- `docker-compose.prod.yml` 內明確定義單一 `postgres`、`redis`、`agentos`、`kachu`、`gateway` 服務
- `README.md` 與 `docs/deploy-runbook.md` 都明示目標模式為單台 Linux 主機
- `Dockerfile` 使用單一 uvicorn 啟動命令，未見 gunicorn 多 worker 或多實例調度配置

### 2. 執行模型

Kachu 主體目前同時承擔以下角色：

- HTTP API / webhook 入口
- workflow tools 執行端
- scheduler 啟動與排程 job 執行者
- LINE webhook background task 執行者
- 外部 API 整合端（LINE / Meta / Google / LLM）

也就是說，目前不是典型的「web app + queue worker + scheduler worker + async job system」分拆架構，而是較集中於單一服務進程中完成。

### 3. AgentOS 角色

AgentOS 目前提供的主要價值為：

- task / run / approval 狀態生命週期管理
- idempotency 與 runtime record
- Kachu workflow 的外部 runtime 邊界

但目前 Kachu 端仍保有大量 orchestration 與 step 邏輯，尚不是完全由 AgentOS 接手調度的成熟分工模式。

## 四、與承載能力直接相關的架構事實

### 1. Scheduler 是 O(租戶數) 掃描

`src/kachu/scheduler.py` 目前會定期執行以下 job：

- 每小時：configured automation dispatch
- 每 5 分鐘：deferred dispatch retry
- 每小時：post performance check
- 每 2 小時：FB comment scan
- 每分鐘：scheduled publish dispatch

其中多個流程都會直接：

- 呼叫 `list_active_tenant_ids()`
- 逐 tenant 順序處理
- 在 tenant 內再查 DB、再打外部 API、再可能推 LINE

這種模式的特性是：

- 租戶數成長時，背景負載線性增加
- 尖峰時段如果多個 job 疊在一起，會共享同一個 app process 的事件迴圈與網路 I/O 資源
- 在租戶數較低時設計簡單且足夠穩定
- 在高活躍租戶數時容易出現延遲累積與 job 漂移

### 2. LINE webhook 使用 BackgroundTasks，而非 durable queue

`src/kachu/line/webhook.py` 在收 webhook 後會先回 `{"status": "ok"}`，再透過 `BackgroundTasks` 執行 `_handle_event_logged`。

此模式的優點：

- webhook 回應快
- 實作簡單
- 對 beta 階段非常實用

此模式的限制：

- background task 與 app process 綁定
- 若 process 在回 200 後發生重啟或擁塞，可靠性不如獨立 queue worker
- 當同一時段大量 webhook 與 scheduler job 並發時，會共享同一執行資源

### 3. 單 process Kachu 承擔 web + background + scheduler

`src/kachu/main.py` 會在 FastAPI lifespan 中啟動 `KachuScheduler`。這表示：

- scheduler 與 HTTP request handler 共享同一個 Kachu process
- 不同負載類型沒有硬隔離
- 對低至中量級 workload 可以接受
- 對高峰期活躍租戶數不利

### 4. 資料庫目前不是第一個瓶頸

從目前程式與 production 驗證來看，PostgreSQL 比較像是穩定的 state store，而非最先爆的元件。原因如下：

- 大多數 query 為 tenant-scoped 或 recent-window query
- 資料模型雖大，但目前操作模式偏中低頻
- audit / push log / workflow runs 雖持續增加，但尚未看到高複雜度 join-heavy 報表查詢成為主路徑
- production 已驗證 PostgreSQL health 正常，且單機寫入量仍在可接受範圍

真正比較可能先成為瓶頸的是：

- 單 process 的事件迴圈負載
- 背景任務與 scheduler 競爭
- 外部 API latency / quota

### 5. Redis 目前主要是輔助，不是核心吞吐中樞

Redis 現況主要用於 OAuth state store 與部分狀態共享，不是完整 job queue 或 distributed task broker。因此它對第一年穩定性有幫助，但尚未承擔真正的背景任務削峰填谷角色。

### 6. LLM 預算表存在，但未看到完整硬性控流

系統已具備 per-tenant LLM budget table（預設 `monthly_budget_usd=5.0`），但目前更偏 observability 與營運控制基礎，尚未呈現完整、嚴格、全面的 runtime hard enforcement。這代表第一年如果租戶使用行為偏高頻，成本與延遲控制仍需靠產品節奏與 feature gating，不宜單純假設系統會自動保護資源。

## 五、產品功能成熟度對承載能力的影響

第一年能撐多少租戶，不應只看技術容量，也要看哪些功能真的已經達到可穩定商用的成熟度。

### 1. 已較成熟、可納入第一年主力價值的能力

- LINE webhook 基本收訊與路由
- Boss approval / edit / reject / postback 流程
- FB 發文主流程
- Knowledge update 主流程
- Proactive monitor / nudge
- Meta insights / comments list / reply 的核心能力
- dashboard audit / workflow / knowledge 可追蹤性

這些能力已經過多輪 production 驗證，是目前可以作為第一年對外主賣點的主體。

### 2. 尚未成熟到可納入大規模承載假設的能力

- Google Business 發文 / 回評論：仍受 GBP 配額與平台核准限制
- GA4 報表：connector / workflow 尚未完整進入商用可擴展階段
- IG：需 `ig_user_id`，相關驗證尚未完整
- hide/unhide comment：需要特定條件，不適合視作高成熟常態能力

因此第一年承載評估應以「LINE + FB + knowledge + approval + nudge」為主，而不是把 Google / IG 全納入成熟供給能力後再估。

## 六、第一年真正的承載指標應該看什麼

本產品不應以「總註冊租戶數」作為第一年容量判斷核心，而應以以下三種租戶數區分：

### 1. 總註冊租戶數

已建立 tenant、但不一定每週使用或開通所有功能。

### 2. 月活躍租戶數

一個月內至少觸發過一次實際工作流，例如：

- 發文
- 更新知識
- 收到 nudge
- 查 insight
- 透過 approval 卡互動

### 3. 高活躍租戶數

每週多次互動、且真的使用自動化、留言監控、推播、排程等功能的租戶。這群租戶才是真正消耗系統資源的主要對象。

本系統的第一年容量評估，應以高活躍租戶數作為主要判斷依據。

## 七、第一年可承載租戶量的區間判斷

以下區間是基於目前架構、已知限制、現有驗證覆蓋、以及本產品「低頻但高價值操作」特性所做的務實估計。

### 區間 A：20 到 50 個高活躍租戶

評估：安全、可支撐。

這個區間下：

- 單機部署仍合理
- scheduler 的 O(租戶數) 掃描成本可接受
- webhook background task 壓力大概率可控
- 外部 API 波動仍可透過營運與人工觀察處理
- production 問題排查成本不至於失控

如果第一年以此作為核心付費客戶群，技術風險與產品節奏是匹配的。

### 區間 B：50 到 120 個高活躍租戶

評估：可行，但進入警戒區，需要營運節奏控管。

這個區間下的前提通常包括：

- 並非所有租戶同時使用重功能
- 垂直產業集中，使用行為較可預測
- 透過 feature flag 控制哪些租戶開啟哪些能力
- Google / GA4 仍不會是高普及主功能
- 有明確的 beta / pilot cohort 管理，不一次性全面放量

在此區間，系統可能仍能運作，但會開始感受到：

- scheduler job 在尖峰時段疊加
- LINE / Meta / LLM latency 放大用戶感知延遲
- 背景任務與即時 webhook 處理互相影響
- 營運排查與人工干預成本上升

### 區間 C：120 到 200 個高活躍租戶

評估：不建議當成穩定承諾區間；屬於勉強可碰但風險偏高。

主要風險：

- 單 process Kachu 很容易成為整體吞吐瓶頸
- scheduler 每分鐘 / 每小時掃描開始出現延遲累積
- 背景任務沒有 durable queue，尖峰期可靠性邊際下降
- 外部 API quota 與延遲容易造成連鎖體驗問題
- 一旦出現 issue，單機部署的故障域過大

若第一年真的打算接近此區間，應視為「需提前做擴容工程的前夜」，而不是現況可安心承諾的能力。

### 區間 D：200 個以上高活躍租戶

評估：不應在現況下對外承諾。

不是因為理論上完全做不到，而是因為：

- 架構隔離不足
- job queue 不夠成熟
- scheduler 與 web 流量共用 process
- 多租戶負載沒有真正水平擴展與任務削峰能力
- 外部 API 配額與延遲已足以放大任何設計短板

## 八、如果看「總簽約租戶」而不是「高活躍租戶」

若第一年多數租戶屬於輕量使用、低頻互動、且不是每週都啟動自動化流程，那總簽約租戶數可以高於高活躍租戶數不少。

較務實的理解方式：

- 20 到 50 個高活躍租戶，可能對應 40 到 100 個總簽約租戶
- 50 到 80 個高活躍租戶，可能對應 80 到 150 個總簽約租戶
- 若超過 150 個總簽約租戶，但其中只有一部分週活躍，現況仍可能勉強運作

但若總簽約租戶數上升的同時，高活躍比例也明顯提高，則很快會碰到上文提到的脆弱區。

## 九、支撐第一年的正面因素

雖然現況不是高擴張型架構，但它對第一年其實有幾個很重要的優勢。

### 1. 產品使用型態天然偏低頻

這不是一個高頻交易、毫秒級 SLA、或海量即時訊息吞吐的產品。大多數操作是：

- 老闆傳一句話
- 系統生成草稿
- 老闆確認
- 系統推播或發文

這種 workload 對單機架構相對友善。

### 2. Approval gate 天然抑制爆量

許多高風險步驟仍需老闆確認，這在產品上是必要流程，但在技術上也等於自然節流，減少系統在短時間連續執行不可逆操作的機率。

### 3. Push 有顯性保護

系統已有 push 日上限與部分 rate-limit / quiet hours 判斷，不會讓單租戶無限度地放大量推播。

### 4. 多租戶核心邊界已落地

tenant membership、tenant-scoped recipient resolve、dashboard tenant-aware 操作、audit event 都已實作完成。這代表第一年不是在單租戶 hack 上硬疊租戶，而是已有正式多租戶基礎。

### 5. 可觀測性已具備商用 beta 基礎

workflow run、approval、audit、dashboard、tenant health snapshot 等能力，讓 production 問題至少有可追查基礎。對第一年少量高價值客戶來說，這非常重要。

## 十、第一年最可能先出現的瓶頸

### 1. Kachu 單 process 事件迴圈壓力

這會是第一個實際瓶頸。不是資料庫先死，而是：

- webhook background tasks
- scheduler jobs
- 對 AgentOS / Meta / LINE / LLM 的網路 I/O

共同競爭同一個進程的資源。

### 2. Scheduler 的全租戶掃描模式

隨著租戶數與功能開通率上升，這會直接增加每輪 job 的執行時間，造成：

- 漂移
- 排程疊加
- 某些租戶被延後處理

### 3. 外部 API latency 與 quota

就算 Kachu 自己撐得住，Meta、LINE、Google、LLM 仍可能先成為實際服務上限。Google 尤其明顯，目前甚至還沒進入全面可商用狀態。

### 4. Durable queue 缺席

目前背景任務不是靠正式 queue broker + worker 執行，因此對尖峰、重試、持久化、削峰的處理能力有限。

### 5. 成本保護仍未完全閉環

雖已有 LLM budget table，但若第一年租戶活躍度上升，成本控管不能只靠觀察，而需要更強的強制節流與 feature gating。

## 十一、第一年應該如何定義「可支撐」

如果從商業與產品角度來看，「可支撐」不應理解為：

- 可以毫無節制地接大量租戶
- 可以把所有功能一次全開給所有租戶
- 可以不做 cohort 管理就直接全面擴張

更合理的定義是：

### 第一年的可支撐狀態

- 以 30 到 80 個高黏著租戶作為主體
- 聚焦 1 到 2 個垂直產業
- 主賣點集中在線上最穩的能力：LINE + FB + knowledge + approval + nudge
- Google / GA4 / IG 仍採 feature-gated 或分階段開放
- 對每個租戶重視 onboarding、留存、品質與實際成果，而不是追求大量帳號數

若以這種模式經營第一年，我認為本系統是有機會支撐起來的，而且具有商業合理性。

## 十二、不建議的第一年策略

以下策略與目前系統形態不匹配：

### 1. 第一年前半年就衝數百高活躍租戶

不建議。風險會集中爆在：

- scheduler 漂移
- webhook 背景任務可靠性
- 外部 API 波動
- 單機故障域

### 2. 尚未完成 Google / IG readiness 就把完整多平台能力作為大規模銷售承諾

不建議。這會讓商業承諾跑在技術成熟度前面。

### 3. 不做 feature gating，所有租戶一律開啟全部自動化與監控功能

不建議。第一年更適合做有節奏的能力釋出。

## 十三、第一年建議承諾區間

### 對內建議目標

- 核心高活躍租戶：30 到 50
- 可接受擴張目標：50 到 80
- 進入高風險區前的上限警戒：80 到 120

### 對外不建議承諾

- 現況下直接承諾 200+ 高活躍租戶穩定服務
- 現況下承諾 Google / GA4 / IG 已全面成熟可大量導入

## 十四、若要把第一年上限再往上推，最先該補的工程

雖然本文件重點不是重構規劃，但若未來要把可承載租戶數從低百位內往上推，以下工程優先順序很高。

### 1. 將 background work 與 scheduler 從 Kachu web process 拆出

至少做到：

- webhook 接收與背景處理解耦
- scheduler 成為獨立 worker
- web / worker / scheduler 不共用單一 process

### 2. 建立真正 durable queue

讓：

- webhook 後續工作
- approval 後續工作
- scheduled publish
- retry / replay / deferred dispatch

都能進入可追蹤、可重試、可削峰的任務系統。

### 3. 把 scheduler 從全租戶掃描改成更事件化或更可分片的模型

例如：

- per-tenant next-fire state
- shard-based scheduler worker
- Redis / queue 驅動的 due item dispatch

### 4. 強化 LLM / platform cost guardrails

包括：

- per-tenant hard limits
- feature tiering
- 更明確的 budget enforcement

## 十五、最終判斷

### 核心結論

本系統目前**足以支撐第一年的早期商用與 pilot 規模**，但這個判斷成立的前提是：

- 第一年的目標是少量到低百位內的活躍租戶，而不是高百位的大規模活躍租戶
- 對外主賣能力以已成熟的 LINE / FB / knowledge / approval / nudge 為主
- Google / IG / GA4 仍採分階段開放
- 營運上做 cohort 控制與 feature gating

### 簡化版結論

- 20 到 50 個高活躍租戶：可支撐，風險可控
- 50 到 120 個高活躍租戶：可行，但需節奏管理
- 120 到 200 個高活躍租戶：偏脆弱，不宜作為穩定承諾
- 200+ 高活躍租戶：現況不建議承諾

### 商業層面的意思

這個產品第一年應該追求的是：

- 深服務型商用 beta
- 高黏著、能產生案例的少量租戶
- 穩定留存與產品磨合

而不是：

- 以大量租戶鋪量作為第一年成功定義

就第一年而言，本系統**不是不夠做生意**，而是更適合走「少量高價值租戶」策略，而不是「大量高活躍租戶」策略。
