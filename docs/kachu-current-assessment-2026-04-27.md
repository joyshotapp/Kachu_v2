# Kachu v2 — 現況總評報告

> 更新日期：2026-04-27  
> 範圍：依據今天多輪 code review、技術債清理、風險修補與局部優化後的版本進行評估。

---

## 一、總結判斷

目前的 Kachu 已經不是概念驗證或單純 demo，而是進入「可持續運行的早期 beta 系統」階段。

它的核心價值已經相對清楚：
- 透過 LINE 作為老闆的指揮入口
- 由後端工作流協調內容生成、審批、發布、評論回覆與營運建議
- 逐步累積品牌知識、偏好與 episodic memory

經過今天的調整後，Kachu 最明顯的提升不在功能數量，而在系統可信度：
- 關鍵 webhook 的授權與失敗路徑更可控
- 降級策略更明確，不再把錯誤偽裝成成功
- 例外分類開始從「全部吞掉」轉向「可恢復錯誤 vs 系統級錯誤」分流
- OAuth state 與部分跨流程狀態已更接近多實例部署需求

一句話總結：

Kachu 現在是一個方向正確、主幹可用、值得持續投資的 agent product backend，但距離穩定商用仍差一段「工程化收尾」。

---

## 二、評分

### 1. 產品方向：8/10

優點：
- 目標客群明確，聚焦微型創業者/店家營運助理場景
- LINE 作為控制入口很務實，降低使用門檻
- Google Business、Meta、GA4、審批、內容生成等模組能組成清楚的產品敘事

保留：
- Blog / 官網發文、評論整合與部分跨渠道能力仍不完整
- Product Plan 與實作之間還有落差

### 2. 技術架構：7/10

優點：
- FastAPI + 工具路由 + workflow 協調的主結構是可工作的
- Settings、repository、memory、外部 client 的分層已成形
- AgentOS、LINE、Google、Meta 等整合面雖然還重，但已不再是完全鬆散拼裝

保留：
- AgentOS 仍是執行層單點依賴
- 部分產品文件仍假設了尚未落地的 LangGraph / Qdrant 主路徑
- 測試與部署工程尚未把架構成熟度補齊

### 3. 實作品質：7/10

今天明顯改善的地方：
- [src/kachu/tools/router.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\tools\router.py) 已把大量 broad exception 收斂，只保留 helper 層的受控轉譯
- [src/kachu/line/webhook.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\line\webhook.py) 已有下載 retry 與更明確的失敗回報
- [src/kachu/auth/oauth.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\auth\oauth.py) 已支援 Redis-backed OAuth state store
- [src/kachu/approval_bridge.py](c:\Users\User\Desktop\Kachu-v2\src\kachu\approval_bridge.py) 已把編輯審批成功/失敗訊號做實，不再永遠回傳空值

保留：
- 仍有若干模組殘留 broad exception 或非精細錯誤分類
- 部分檔案格式與風格仍偏粗糙，存在歷史技術債痕跡

### 4. 上線成熟度：6/10

優點：
- 生產設定驗證、schema guard、webhook 驗證與降級策略比之前可靠很多
- 主要高風險隱性失敗點已被清理一批

不足：
- CI/CD 尚未建立
- lint / type check 尚未納入流程
- runtime 測試環境尚未在本機穩定重現
- migration 流程雖已起步，但還不是完整上線體驗

---

## 三、今天版本的主要進步

### 1. 系統行為更誠實

先前有一些路徑會把失敗包裝成看似正常的回應，今天已大幅修正。現在 Kachu 比較少出現「其實失敗了，但表面像成功」的情況。

### 2. 外部整合失敗更可觀測

不論是 LINE 下載、Google webhook、OAuth state、內容發布還是 LLM fallback，現在都更接近可追查、可解釋的狀態。

### 3. 多實例與正式部署考量開始進場

像 OAuth state store 改成可走 Redis、production config guard、schema create 保護，這些都代表系統開始考慮真正部署時的行為，而不只是單機開發模式。

### 4. 例外處理水準上升

今天最有價值的工程進展之一，是逐步把 broad exception 從業務邏輯中清走，改成明確區分：
- 可恢復的外部依賴錯誤
- 可降級的格式/回應問題
- 不該吞掉的系統級 bug

這對長期維護非常重要。

---

## 四、目前仍限制 Kachu 往上走的關鍵短板

### 1. 工程化基礎設施還不夠

沒有 CI、沒有 lint/type-check gate、沒有穩定可重現的 runtime 測試，會讓後續迭代成本偏高。

### 2. 文件與真實架構仍未完全對齊

LangGraph、Qdrant 等敘述與現況仍有差距。這不是表面文件問題，而是會直接影響團隊對產品成熟度的判斷。

### 3. AgentOS 依賴仍偏重

只要 AgentOS 失效，Kachu 的主工作流就會大幅降級。這代表它目前仍偏 orchestration-heavy，而不是具備明確局部容錯的系統。

### 4. 剩餘模組仍有歷史債

例如 approval / scheduler / memory / dashboard / intent routing 一帶還有一些需要持續清的錯誤處理與可觀測性問題。

---

## 五、目前定位

若從產品成熟度來看，我會這樣定位 Kachu：

- 不是 PoC
- 不是一次性 demo
- 是可運行的早期 beta
- 尚未達到穩定商用等級

如果接下來補上：
- 測試環境
- CI/CD
- 靜態品質檢查
- 剩餘模組的錯誤分類與部署契約

那它會很快從「值得持續投資的 beta」進入「可控風險的商用候選版本」。

---

## 六、建議的下一步

### 短期（本週）

- 補齊可執行的 pytest 環境與依賴
- 把剩餘高頻 broad exception 模組再清一輪
- 補 `.env.example` / runbook，對齊 Redis OAuth state 與部署契約

### 中期（下個 Sprint）

- 補 lint / type-check / CI
- 整理 Product Plan 與真實架構落差
- 把 migration 流程從 scaffold 提升到可實際操作

### 較後期

- 決定是否真的導入 LangGraph / Qdrant
- 降低 AgentOS 單點依賴
- 強化 observability 與營運級告警
