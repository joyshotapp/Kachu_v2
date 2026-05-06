# Kachu v2 技術面完整評估報告

日期：2026-05-06  
範圍：基於 repo 全面 code review、production 驗證結果、測試覆蓋、技術債清單與架構事實

---

## 一、評估基準

本文件依據以下事實依據撰寫，不是假設性的理想設計評估：

- `tests/` 21 個測試檔，305 個 test case，全部通過（7.12s）
- `alembic/versions/` 9 次正式 migration，從 2026-04-27 至 2026-05-05
- production 已於 2026-05-06 驗證的項目：135 個 ✅，尚未驗證項目：70 個（多數依賴 GBP quota / IG `ig_user_id` 等外部前置條件）
- `docs/tech-debt-and-issues.md`：已知 open / partial 問題清單
- `docs/kachu-current-assessment-2026-04-27.md`：上一次評估基準
- production 主機：`172.234.85.159`，Docker Compose 單機部署，6 個 container 均 healthy

---

## 二、系統整體定位

Kachu v2 目前的定位是：**可運行的早期 beta 系統，具備商用基礎，但尚未到穩定商用等級。**

這個判斷的依據是：

- 主要業務流程已在 production 驗證通過（發文、審批、知識更新、nudge、Meta insights、留言回覆）
- 系統已有多租戶基礎、audit trail、tenant-scoped 操作，不再是單租戶 hack
- 但執行模型是單進程集中式，無 durable queue，無 CI/CD，無靜態品質保護
- 對外承諾的外部 API 能力仍有平台審批阻塞（Google GBP）

---

## 三、架構現況

### 3-1. 部署型態

| 元件 | 現況 |
|------|------|
| 主機 | 單台 Linux，Docker Compose |
| Kachu | 單一 uvicorn process，port 8001 |
| AgentOS | 獨立 container，port 8000 |
| PostgreSQL | 單一實例，`kachu` + `agent_platform` 兩個 DB |
| Redis | 單一實例，OAuth state store + partial shared state |
| Nginx | reverse proxy，TLS by Let's Encrypt |

**評估：部署架構對 beta 與早期商用是合理的，但沒有 HA、沒有備援、沒有水平擴展能力。**

### 3-2. 執行模型

目前 Kachu 主 process 同時承擔：

1. HTTP API 入口
2. LINE webhook 處理 + BackgroundTasks
3. workflow tool endpoints（`/tools/*`）
4. APScheduler 排程執行
5. 所有外部 API 呼叫（LINE / Meta / LLM / AgentOS）

這是一個典型的「單體式集中架構」。優點是開發快、部署簡單。缺點是資源沒有隔離，負載類型不同但共用同一個事件迴圈。

**`src/kachu/main.py`** 的 lifespan 直接啟動 `KachuScheduler`，這讓 scheduler jobs 與 webhook handling 共用同一個 process。

**`src/kachu/line/webhook.py`** 使用 FastAPI `BackgroundTasks`，webhook 回 200 後才非同步執行業務邏輯。這對 beta 非常實用，但不是 durable task queue。

**評估：這種架構在租戶數低時可接受，租戶數增加後最先感受壓力的不是資料庫，而是這個 process 的事件迴圈資源。**

### 3-3. AgentOS 整合現況

AgentOS 目前承擔：
- task / run 生命週期記錄
- approval state 管理
- idempotency（透過 `idempotency_key`）

Kachu 端仍保有：
- 所有 orchestration 邏輯（step 順序、分支、外部 API 決策）
- `kachu_workflows/*.py` 只是 Step 清單 stub，無真正 flow control

**評估：AgentOS 發揮的是「狀態記帳員 + approval 門衛」的作用，而不是真正的 orchestration engine。這不是錯的，但代表目前 Kachu 的 workflow 可靠性還是依賴自身 `router.py` 的執行路徑，而非 AgentOS 的 retry / timeout 保護。**

### 3-4. 資料層

- ORM：SQLModel + SQLAlchemy
- Migration：Alembic，9 個正式版本，HEAD = `20260505_0009`
- 多數查詢是 tenant-scoped + recent-window，對目前規模負擔合理
- 沒有看到高複雜度 join 或 cross-tenant aggregate 在主路徑

**評估：資料層目前不是第一瓶頸，但 `kachu_audit_events`、`kachu_workflow_runs`、`kachu_push_logs` 等高寫入表需要提早設定 retention 策略，避免長期積累成查詢問題。**

---

## 四、測試覆蓋評估

### 4-1. 覆蓋量化

| 指標 | 數值 |
|------|------|
| 測試檔數 | 21 個 |
| test case 數 | 305 個 |
| 全部通過 | ✅ 7.12s |
| production 驗證項目 | 135 個 ✅ / 70 個待補 |

### 4-2. 覆蓋強項

- Phase 1–6 主流程的 contract / parity test 已有對應測試
- approval / postback / edit / reject 路徑已有 test
- onboarding flow、knowledge update、memory 系列均有測試
- 安全性 guard（signature check、tenant boundary）有回歸保護
- meta insights / schema contracts 均有 unit level 保護

### 4-3. 覆蓋缺口

- 沒有 integration test 能完整驗證 scheduler → external API → LINE push 整條流程
- BackgroundTasks 的異步行為無法在 unit test 完整覆蓋
- Kachu ↔ AgentOS 的 cross-service contract test 尚薄（boundary contract 有文件，但測試力度不均）
- 沒有 performance test / load test
- 沒有 CI pipeline 自動執行這些測試

**評估：305 個測試全通過是目前最重要的品質保證，但缺 CI 意味著這些保護只有在手動跑時才生效，不是持續保護。**

---

## 五、工程品質評估

### 5-1. 已達到的品質水準

**錯誤處理分類（重要進步）**：`docs/tech-debt-and-issues.md` 記錄的 FIXED-9 到 FIXED-17 系列都已完成，代表系統不再用 broad exception 吞掉大部分錯誤，而是明確區分：

- 可恢復的外部依賴錯誤（LLM / HTTP / DB）
- 可降級的 fallback 路徑
- 不該吞的系統級 bug

**安全性基礎**：
- LINE webhook signature 驗證
- dashboard admin token 驗證
- production config validation（啟動時強制檢查必填設定）
- OAuth state 用 Redis TTL 防 CSRF
- `DANGEROUSLY_SKIP_SIGNATURE_CHECK` 等 escape hatch 明確限制在 test

**多租戶隔離**：
- tenant membership 已正式落地
- 所有 data access 均 tenant-scoped
- recipient resolve 已從 `LINE_BOSS_USER_ID` legacy 模式遷移到 membership-based

**可觀測性基礎**：
- audit event 遍及所有主要操作
- workflow run status 可追蹤
- dashboard API 可查 tenant health snapshot、connector 狀態、audit events

**Migration 管理**：
- 9 個正式 Alembic migration，production 啟動前會 `assert_schema_migrated`
- 禁止 production 自動 `create_all()`

### 5-2. 仍存在的技術問題

以下依照 `docs/tech-debt-and-issues.md` 現況整理：

#### 安全性問題

| 代號 | 問題 | 狀態 | 嚴重度 |
|------|------|------|--------|
| SEC-1 | `.env.prod` 含真實憑證，需確認是否已輪替、是否曾 commit | partial | Critical |
| SEC-2 | Google review webhook OIDC 驗證尚未在 production 正式啟用 | partial | High |
| SEC-3 | production config validation 仍有條件未完整明文化 | partial | High |

#### 架構問題

| 代號 | 問題 | 狀態 | 嚴重度 |
|------|------|------|--------|
| ARCH-1 | Product Plan 仍提及 LangGraph，但主路徑未用，文件與實作不一致 | open | Medium |
| ARCH-2 | Qdrant 未進入主路徑，但產品文件有提及 | open | Medium |
| ARCH-3 | AgentOS 是執行層單點依賴，無明確 circuit breaker | open | Medium |
| ARCH-4 | Migration 已建立，但 production opt-in escape hatch 仍存在 | partial | Medium |
| ARCH-5 | Langfuse 仍直接讀 `os.environ`，未納入 Settings | open | Medium |

#### 程式碼品質

| 代號 | 問題 | 狀態 | 嚴重度 |
|------|------|------|--------|
| CODE-2 | `_strip_json_fence` helper 部分路徑仍手寫同類邏輯 | open | Low |
| CODE-3 | 無 ruff / mypy / pyright 靜態品質流程 | open | Low |
| TEST-1 | 無 CI pipeline，305 個測試只在手動執行時保護 | partial | Low |

---

## 六、與 Beta → 商用之間的距離

### 6-1. 現在已經具備的商用基礎

以下是目前系統**已達到**讓早期商用 beta 可運作的條件：

1. 主要業務流程 production 驗證通過
2. 多租戶邊界正式落地（不是 hack 疊加）
3. 安全性基礎：signature 驗證、config guard、OAuth CSRF 防護
4. Audit trail：任何重要操作都有記錄可查
5. 305 個測試全通過，主要路徑有回歸保護
6. 9 個 Alembic migration，schema 有正式管理
7. Dashboard 可進行 tenant 管理、健康檢查、export、deactivate、delete

### 6-2. 與穩定商用等級的差距

以下是目前系統**仍不足以**支撐穩定大規模商用的點：

1. **執行隔離缺失**：web / scheduler / background job 共用單一 process，無硬隔離
2. **無 durable queue**：LINE webhook 後續工作靠 BackgroundTasks，不是可重試的任務系統
3. **Scheduler 全租戶掃描**：O(租戶數) 線性掃描，租戶多時直接加壓主 process
4. **無 CI/CD**：沒有自動化測試門禁，質量保護依賴手動執行
5. **無 lint/type-check**：型別錯誤與風格問題無法被系統性預防
6. **AgentOS 單點無 circuit breaker**：AgentOS 不可用時缺乏優雅降級
7. **LLM cost guardrails 尚未強制執行**：budget table 存在，但 runtime hard limit 未完整閉環
8. **文件與實作落差**：LangGraph / Qdrant 敘述仍殘留在產品規劃文件
9. **SEC-1 未完整解決**：憑證安全性仍有未確認的風險

---

## 七、技術風險矩陣

| 風險 | 發生可能性 | 影響嚴重度 | 建議優先度 |
|------|-----------|-----------|-----------|
| Kachu process OOM / 事件迴圈壓力導致所有服務降級 | 中（低租戶數時低，高租戶數後中高） | 極高（所有功能同時受影響） | 最高 |
| LINE webhook BackgroundTask 在 process 重啟時丟失 | 中 | 高（租戶操作靜默失敗） | 高 |
| Scheduler job 漂移 / 疊加導致 tenant 被延後處理 | 中（高租戶數後上升） | 中（SLA 延遲，不是數據錯誤） | 高 |
| AgentOS 不可用導致所有 workflow dispatch 失敗 | 低（目前 healthy） | 極高（全面降級） | 高 |
| `.env.prod` 憑證曾暴露且未輪替 | 不確定 | 極高（安全事件） | 緊急 |
| Google GBP quota 長期無法解鎖 | 中 | 高（GBP 相關功能無法商用） | 中 |
| LLM 成本失控 | 低（活躍度低時） | 中（營運成本問題） | 中 |
| PostgreSQL 查詢因資料量增長變慢 | 低（目前規模小） | 中（查詢延遲） | 低（但要提前設計） |

---

## 八、工程投資優先順序建議

以下依照投資效益比排序，不是技術理想度排序。

### P0：立即應做（安全性 / 商用前置）

**1. 確認並輪替 `.env.prod` 所有憑證**

如果這份檔案曾被 commit，或在任何非安全環境中被讀取，所有 token / password 都應視為已暴露。這是唯一有可能導致真實安全事件的項目。

**2. 建立 CI pipeline（即使是最簡單的）**

至少讓 `pytest tests/` 在每次 push / merge 時自動執行。這一步幾乎不需要重構，但會從根本上改變質量保護的有效性。可選工具：GitHub Actions / GitLab CI。

### P1：近期（從 beta 走向穩定商用前）

**3. 把 web、scheduler、background jobs 執行隔離**

不需要完整重構，最小可行步驟：

- 把 scheduler 移出 Kachu lifespan，改成獨立 Docker service 或獨立 uvicorn worker
- 把 LINE webhook 的 BackgroundTasks 改為寫入 Redis Queue / Celery / ARQ，由獨立 worker 處理

完成這一步後，主 Kachu process 只需要處理 HTTP，其他工作有自己的資源與可觀測性。

**4. 導入 durable job queue**

建議方案：`ARQ`（基於 Redis，輕量，與現有架構兼容）或 `Celery`（更成熟）。

要進入 queue 的工作：
- LINE webhook 後續業務處理
- approval 後續通知
- scheduled publish
- deferred dispatch retry
- post performance scan

**5. 補 lint / type-check**

先加 `ruff`，只需要在 `pyproject.toml` 加配置並在 CI 執行。即使不追求 100% 通過，一開始設成 warn-only 也有很大價值。

### P2：中期（提升穩定性與可信度）

**6. Scheduler 全租戶掃描改為事件化**

核心改動：

- 每個 tenant 的下次執行時間 persisted 到 DB
- scheduler 只 query due tenants，不是全量掃描
- 大幅降低 scheduler 在租戶數增加時的線性成本

**7. AgentOS 單點依賴加 circuit breaker**

最小可行方案：在 `AgentOSClient` 加計數器，連續失敗超過閾值後進入 open circuit，讓 Kachu 對 boss 回應 degraded 訊息，而不是 silently hang。

**8. Timeout / retry / idempotency 全鏈路一致化**

目標：任何一個 workflow step 失敗，都有明確的 retry policy、fallback 行為、與 audit 記錄，且這些行為是一致的，不依賴個別 endpoint 的各自處理方式。

**9. LLM cost guardrails 強制閉環**

- per-tenant monthly hard limit 要真正 enforce，不只是記錄
- 超標後有明確降級行為（例如暫停自動化，保留人工觸發）
- 這會直接影響未來多租戶的成本預測可信度

### P3：長期（架構升級）

**10. AgentOS 潛力充分使用**

目前 `kachu_workflows/*.py` 只是 Step 清單 stub。若 AgentOS 真的成為有 retry / timeout / branching 的 orchestration engine，Kachu 的 `router.py` 可以大幅瘦身，workflow 可靠性也可以提升。這需要定義 AgentOS 自身的功能路線後再評估。

**11. Qdrant 的技術決策**

目前使用 in-process cosine similarity，對現有規模夠用。若未來 KB 增大或需要更高召回率，Qdrant 是合理的升級路徑。但現在不是必要的，不應為此花工程資源，除非 KB retrieval 品質成為已知產品問題。

**12. 水平擴展準備**

若租戶數達到上百個高活躍，需要考慮：
- Kachu 多實例（需要確認 scheduler 不重複執行、session 不 race）
- PostgreSQL 從單機到具備 replica 的配置
- 評估是否需要遷移到 managed infrastructure

---

## 九、目前最值得投資的四件事

如果只能選四件技術事，按優先序排列：

1. **SEC-1 憑證輪替確認**：安全性優先於一切其他工程優先級。
2. **CI pipeline 建立**：305 個測試需要自動執行才有持續保護效果。
3. **Web / scheduler / background 執行隔離**：對穩定性的投報比最高。
4. **Timeout / retry / idempotency 全鏈路一致化**：降低多租戶環境下不可預期行為的頻率。

這四件事做完，系統會從「可跑的 beta」升級成「可信賴的早期商用」。

---

## 十、技術成熟度評分（2026-05-06）

| 面向 | 評分 | 說明 |
|------|------|------|
| 產品方向 | 8/10 | 定位清楚，場景真實，有 production 驗證 |
| 技術架構 | 6.5/10 | 主幹可用，但執行隔離不足，AgentOS 整合淺 |
| 實作品質 | 7/10 | 錯誤處理明顯改善，仍有技術債 |
| 測試覆蓋 | 7/10 | 305 個測試全通過，但缺 CI 且有覆蓋盲區 |
| 安全性 | 6/10 | 基礎已有，但 SEC-1 未確認是上限 |
| 可觀測性 | 7.5/10 | Audit + dashboard + health snapshot 已成形 |
| 上線成熟度 | 6/10 | Production 驗證通過，但缺 CI/CD / 靜態品質 |
| 可擴展性 | 5/10 | 單進程集中式，無 durable queue，擴展路徑待建 |

**綜合評分：6.5/10**

這是一個「方向正確、主幹可用、值得繼續投資」的系統，不是一個需要重來的系統。但它還差一個工程化收尾的階段，才能成為可自信承諾商用的版本。

---

## 十一、結論

### 一句話技術評估

Kachu v2 目前是一個**可運作的早期 beta 系統，具備商用所需的功能基礎與多租戶基礎，但執行可靠性、工程品質門禁、以及架構隔離三者仍不足以支撐穩定的大規模商用**。

### 從 beta 到穩定商用需要的三件事

1. **安全性收尾**：SEC-1 憑證確認、SEC-2 OIDC 正式啟用
2. **工程品質門禁**：CI pipeline + lint/type-check 自動化
3. **執行模型升級**：執行隔離 + durable queue

這三件事做完，Kachu 會從「值得投資的 beta」進入「可控風險的商用候選版本」。

### 不需要做的事

- 不需要大幅度重寫
- 不需要現在就評估 LangGraph 或 Qdrant
- 不需要現在就做水平擴展
- 不需要犧牲目前可工作的功能來追求架構完美

### 適合的工程節奏

**現在**：確認安全性（SEC-1）→ 建 CI → 加 lint  
**近期**：執行隔離 → durable queue → idempotency 全鏈路  
**中期**：scheduler 事件化 → AgentOS circuit breaker → LLM cost enforce  
**長期**：水平擴展準備 → AgentOS orchestration 升級 → Qdrant 評估決策
