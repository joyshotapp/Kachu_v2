# Hermes Agent 研究與評價

更新日期：2026-05-03

## 研究方法與範圍

這份筆記分成兩條來源交叉驗證：

1. 網路公開資訊
   - GitHub repository 主頁與 README
   - 官方文件站：architecture、memory、skills、messaging、security
2. 本地原始碼檢視
   - 本機 clone 路徑：`/Users/yuchuchen/Desktop/hermes-agent`
   - 實際閱讀 `run_agent.py`、`gateway/run.py`、`hermes_state.py`、`tools/approval.py`、`tools/skill_manager_tool.py`、`tools/session_search_tool.py`、`cron/scheduler.py`、`agent/memory_manager.py`、`tools/registry.py`

這不是一次完整 audit，也不是跑完整安裝與 end-to-end 體驗；它是「官方敘述 vs 本地實作」的對照式認識。

## 網路上 Hermes Agent 的主描述

Hermes Agent 對外把自己定位成：

- 自我成長的 AI agent
- 具有內建 learning loop，不只是聊天介面
- 可在 CLI、訊息平台、雲端沙箱、VPS、GPU cluster 等多種環境運行
- 具備技能系統、記憶系統、跨 session 搜尋、排程、子代理委派、MCP 擴充
- 有比較強的安全邊界：dangerous command approval、container isolation、MCP 憑證過濾、prompt injection 掃描、SSRF 保護

從 GitHub 頁面可見的社群信號也很強：

- 約 131k stars
- 約 19.8k forks
- 700+ contributors
- 最新 release 在 2026-04-30 左右，版本 `0.12.0`

這些數字不能直接證明品質，但至少代表它不是冷門玩具專案。

## 我在本地 repo 看到的核心架構

### 1. 它不是單一 CLI 腳本，而是一個大型 agent 平台

官方 architecture 文件與本地原始碼彼此對得上。

主要 entry points 包含：

- CLI
- Gateway
- ACP / editor integration
- Batch runner
- API server
- Python library 使用方式

核心中樞是 `AIAgent`，實作在 `run_agent.py`。`run_conversation()` 負責：

- system prompt 組裝
- provider / model runtime resolution
- tool calling loop
- interruption handling
- context compression
- persistence
- 背景 memory / skill review 觸發

也就是說，Hermes 的核心不是「很多散工具」，而是確實有一個統一的 agent orchestration loop。

### 2. 它的 tools 與 skills 不是裝飾，而是第一級架構元素

`tools/registry.py` 顯示 Hermes 使用集中式 tool registry。工具模組在 import 時以 `registry.register()` 自動註冊，`model_tools.py` 再統一收集 schema、handler、availability check。

我在本地 repo 做的快速量化：

- `tools/` 下的 `registry.register(...)` 呼叫約 68 筆
- `skills/` 內的 bundled skills 約 89 個 `SKILL.md`
- `optional-skills/` 內的官方選配 skills 約 60 個 `SKILL.md`

這代表它的 skill / tool 生態不是 README 寫好看的，而是真的大量存在。

### 3. 它的跨 session 記憶是真的有兩層，不只是 marketing 字眼

官方文件把記憶分成：

- bounded curated memory：`MEMORY.md` / `USER.md`
- session search：SQLite + FTS5 查歷史對話
- optional external memory providers

本地實作也對得上：

- `agent/memory_manager.py`：內建 provider + 最多一個 external provider 的協調器
- `hermes_state.py`：SQLite state store，含 FTS5 與 trigram FTS5
- `tools/session_search_tool.py`：從 SQLite 搜尋舊 session，再用輔助模型做摘要

這裡有一個值得肯定的點：它沒有把所有「記憶」混成一件事，而是把：

- always-in-prompt 的短記憶
- on-demand 的舊對話回憶
- 更深層的外掛 memory provider

拆開處理。這種設計比很多 agent 專案只寫一句「we support memory」紮實很多。

### 4. 它的「自我學習」核心其實是 skill 化，而不是神秘黑箱

這是我覺得 Hermes 最值得認真看的地方。

官方 README 說它有 built-in learning loop。實際讀碼後，我認為這句話不是空話，但要正確理解：

- 它不是自動幫自己重訓模型
- 它也不是會神奇地自己變更聰明
- 它真正的 learning loop 是：把成功流程、踩坑修正、非平凡工作方法沉澱成 skill

支撐這件事的實作包括：

- `tools/skill_manager_tool.py`
  - `skill_manage(create|patch|edit|delete|write_file|remove_file)`
  - schema 內明講 skills 是 procedural memory
  - 也明講何時該 create、何時該 patch
- `run_agent.py`
  - 追蹤 tool-calling iterations
  - 在合適條件下觸發 background review
- `agent/curator.py`
  - 會在 agent idle 時對 agent-created skills 做背景維護
  - 可以做 pin / archive / consolidate / patch

所以 Hermes 所謂的 self-improving，比較準確的說法是：

「它把 agent 在任務中摸索出來的程序知識，往 skill 這種可再利用 artifact 轉化，並且持續維護這批 skill。」

這種設計很務實，也比空泛的“agent learns from experience”更可落地。

### 5. 它的 messaging gateway 不是附屬插件，而是產品級子系統

官方文件強調 CLI 與 messaging 是雙入口，這在本地實作也成立。

`gateway/run.py` 顯示 GatewayRunner 處理：

- session routing
- per-chat session store
- authorization
- slash commands
- cron ticking
- service lifecycle
- background sessions

官方 messaging 文件列出大量平台；本地 `gateway/platforms/` 也確實是一大塊系統，而不是只包一個 Telegram bot。

這代表 Hermes 的真實產品心智不是「本地 coding agent」，而比較接近：

「一個能同時活在 CLI 與多訊息平台的長生命 agent runtime。」

### 6. 安全模型是它的強項之一，而且有實作深度

官方 security 文件不是只有 checklist，本地程式也能看到相對完整的防線：

- `tools/approval.py`
  - dangerous command patterns
  - hardline blocklist
  - approval mode / yolo mode
  - session-scoped approval state
- `gateway/run.py`
  - gateway stale-code self check
  - session freshness gating
  - authorization / pairing 邏輯接點
- 官方文件還描述了：
  - container isolation
  - env passthrough allowlist
  - credential file passthrough
  - MCP env filtering / redaction
  - SSRF 防護
  - context file injection scanning
  - tirith pre-exec scanning

我不能在這份筆記裡證明每個 security claim 都百分之百無缺陷，但從設計與碼量來看，Hermes 確實把安全當成主要產品能力之一，而不是事後補註。

### 7. 排程與背景任務是真的 agent-native，而不是單純 shell cron

`cron/scheduler.py` 和官方文件都顯示：

- scheduler 每 60 秒 tick 一次
- job 是 agent prompt / skill / delivery target 的組合
- 可送回 messaging 平台
- 不是純 shell task scheduler

這一點很重要。Hermes 把 scheduled automations 放在 agent runtime 內，而不是外掛一個 cron + script 的說明書。

## Hermes 如何達成它對外的描述

如果用一句話總結它的機制，我會這樣描述：

Hermes 並不是靠單一「超強 prompt」達成宣傳效果，而是靠一個厚重的平台層，把 agent loop、tool registry、skills、memory、gateway、scheduler、安全邊界、provider routing 都做成長期可運作的系統。

更具體地說：

### 它如何達成「自我成長 / self-improving」

- 用 `skill_manage` 把任務經驗轉成 SKILL.md 與支援檔
- 用 curator 對 agent-created skills 做後續整理與維護
- 用 bounded memory + session search + external memory providers 讓 agent 不只依賴當前 context window

### 它如何達成「可以在很多地方運行」

- 核心 `AIAgent` 平台無關
- CLI、gateway、ACP、batch 都共用同一個核心
- terminal backend 支援 local / docker / ssh / modal / daytona / singularity 等不同執行邊界

### 它如何達成「跨平台互動」

- gateway 把各平台 adapter 收斂到 per-chat session store + unified dispatch
- slash commands 與 busy/interrupt/background 行為在不同平台上保持相近語意

### 它如何達成「長期記憶」

- 小而可控的 MEMORY / USER profile 注入 system prompt
- SQLite + FTS5 / trigram FTS5 做歷史 session 搜尋
- 需要時再用輔助模型壓縮成可用摘要

### 它如何達成「安全可運行」

- host 直跑時用 dangerous command approval
- container / sandbox 路徑用執行邊界取代 approval
- gateway 端加上 allowlist / pairing / secret filtering / SSRF / injection scanning

## 我對 Hermes Agent 的評價

### 總評

Hermes Agent 不是普通的「LLM + tools」side project，而是一個明顯已經進入平台化階段的 agent system。

它最強的地方不是單次回答品質，而是它把很多其他 agent 專案只停在概念圖的東西，真的做成可以運行的子系統：

- tool registry
- skills as procedural memory
- gateway as long-running multi-platform runtime
- session persistence + search
- cron
- approval/security

### 我認為它真正厲害的點

1. 它把「技能」當成 agent 的第一級知識單位，而不是只是 prompt snippet。

2. 它把 CLI、messaging、scheduler、ACP、API server 都收斂到同一個核心 agent loop，產品面與工程面比較一致。

3. 它對安全與授權的重視程度，比大多數開源 agent 專案高很多。

4. 它不只追求桌面互動，而是明顯朝長生命、可部署、可跨平台的 agent runtime 方向發展。

### 我保留的地方

1. 「唯一有 built-in learning loop 的 agent」這種說法我不會原封不動照單全收。
   - 我認同它有很明確的 learning loop 設計
   - 但“唯一”這種宣稱比較像 marketing，不是我這次能證實的事實

2. 它的規模很大，複雜度也很高。
   - 單一檔案如 `run_agent.py`、`cli.py`、`gateway/run.py` 都很大
   - 這種系統功能很多，但維護難度也高

3. 我這次讀到的網路資訊大多來自官方來源。
   - 我看到了社群規模與官方 docs
   - 但沒有在這次研究中額外驗證第三方 benchmark、外部獨立 review 或長期 production case study

4. 它的「功能極廣」同時也是風險。
   - 支援越多平台、provider、sandbox、plugins，整體穩定性與組態複雜度就越難控

## 我會如何定位 Hermes Agent

如果要我用一句比較精確的話定位它：

Hermes Agent 是一個偏「agent operating system / long-running agent platform」取向的系統，而不只是 coding assistant。

它的核心價值不是某個單點功能，而是把：

- 能力擴充
- 記憶沉澱
- 跨平台互動
- 安全執行
- 長期運作

放到同一個架構裡。

## 我的最終評價

如果只問「它是不是名過其實？」

我的答案是：不是。

它至少在我這次讀到的本地實作層面，確實有足夠厚度支撐大部分官方描述。尤其是：

- skills / procedural memory
- gateway runtime
- session persistence + recall
- approval/security

這幾塊，都不是假功能。

如果問「它是不是我目前看過較成熟的一類開源 agent 系統？」

我的答案是：是，而且成熟度顯著高於很多只有 demo 感的 agent repo。

如果問「我會不會完全照單全收它的宣傳話術？」

不會。

我的保留主要在兩點：

- “唯一”這種強勢措辭屬於 marketing
- 真正 production 級穩定度還是要靠實際安裝、配置、長時間運行與失敗場景驗證

但就這次「網路資訊 + 本地原始碼交叉閱讀」的結果，我對 Hermes Agent 的結論是：

它是一套認真、有體系、而且明顯已經超過一般開源 agent 玩具階段的系統；若把它理解成一個 agent platform，而不只是聊天殼，它的很多設計就會變得合理，而且也更值得尊重。