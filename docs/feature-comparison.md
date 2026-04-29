# 功能比較：我的理想 Agent vs Kachu v2 現況

> 更新日期：2026-04-27

---

## 一、功能對照表

| 功能 | 理想需求 | Kachu v2 現況 | 缺口說明 |
|------|---------|--------------|---------|
| **LINE 作為指揮介面** | ✅ 透過 LINE 下指令 | ✅ 已實作 | 無缺口 |
| **AI 寫貼文草稿** | ✅ 幫我寫文 | ✅ 已實作（多版草稿） | 無缺口 |
| **人工確認才發文** | ✅ 確認後才發 | ✅ Approval Gate（HITL） | 無缺口 |
| **發文到 Google Business 最新動態** | ✅ | ✅ 已實作（Workflow 3） | 無缺口 |
| **發文到 Facebook 粉專** | ✅ | ✅ 已實作（MetaClient） | 無缺口 |
| **發文到 Instagram** | ✅ | ✅ 已實作（MetaClient） | 無缺口 |
| **發文到網站 Blog** | ✅ | ❌ **尚未實作** | 缺 WordPress / Ghost 發文整合 |
| **Google 評論通知** | ✅ 有評論通知我 | ✅ 已實作（Google Webhook） | 無缺口 |
| **AI 代回 Google 評論** | ✅ 幫我回 | ✅ 已實作（Workflow 2） | 無缺口 |
| **Facebook 評論/留言通知** | ✅ | ❌ **尚未實作** | 缺 Meta Webhook 訂閱評論事件 |
| **Facebook 評論 AI 代回** | ✅ | ❌ **尚未實作** | 依賴上面的通知機制 |
| **照片上傳自動生成貼文** | ✅ | ✅ 已實作（Workflow 1） | 無缺口 |
| **流量/業績報告** | — | ✅ 已實作（GA4 Workflow 4） | Kachu 額外有此功能 |
| **顧客 LINE FAQ 自動回覆** | — | ✅ 已實作（Workflow 5） | Kachu 額外有此功能 |
| **知識庫管理** | — | ✅ 已實作（Workflow 6 + RAG） | Kachu 額外有此功能 |
| **排程自動發文** | — | ✅ 已實作（Scheduler） | Kachu 額外有此功能 |

---

## 二、缺口詳細說明

### 缺口 1：網站 Blog 發文整合

**你的需求：** 同一篇貼文也能推送到官網 Blog  
**目前狀況：** MetaClient 處理 FB/IG，GoogleBusinessClient 處理 Google，但沒有 Blog 客戶端  
**建議方案：**
- WordPress → 使用 WordPress REST API（`/wp-json/wp/v2/posts`）
- Ghost → 使用 Ghost Admin API
- 自架 → 看後端 framework 決定

**實作難度：** ★☆☆（API 都很成熟，套上現有 Workflow 1 架構即可）

---

### 缺口 2：Facebook 評論 / 留言通知與 AI 代回

**你的需求：** FB 貼文有留言或評論時，通知我並幫我回  
**目前狀況：** MetaClient 只做發文，沒有訂閱 Webhook 事件  
**建議方案：**
- 訂閱 Meta Webhook 的 `feed` 事件（包含留言、評論、按讚）
- 收到事件 → 觸發類似 `kachu_review_reply` 的 workflow
- 推播 LINE 通知 → AI 起草回覆 → 老闆確認 → 代回

**實作難度：** ★★☆（需要 Meta App Webhook 設定 + 新 workflow）

---

## 三、總結

```
你的理想需求 ≈ Kachu v2 已有 80%

主要缺口：
  1. Blog 發文（最容易補）
  2. FB 評論通知與回覆（需要新 Workflow）
```

Kachu v2 甚至還多了你沒提到但很實用的功能：
- GA4 週報自動分析
- 顧客 FAQ 自動回覆
- 知識庫 RAG 記憶（品牌語氣、價格等）
- 排程自動觸發（不用你每週提醒）

---

## 四、建議優先順序

| 優先 | 任務 | 效益 |
|------|------|------|
| 🔥 高 | 補上 FB 評論 Webhook + reply workflow | 讓 FB 管理閉環 |
| 🟡 中 | 串接 Blog 發文（WordPress/Ghost） | 一鍵三平台同步 |
| 🟢 低 | Line@ 顧客留言的 FB/IG 跨平台整合 | 統一客服入口 |
