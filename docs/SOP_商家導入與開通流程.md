# Kachu+ 商家導入 SOP
## 文件入口與角色分流

版本：v2.0  
更新日期：2026-05-09  
適用對象：Kachu+ 工程師、導入人員（CS/BD）

---

這份文件不再同時承載所有角色的細節，而是作為入口索引。

## 你該看哪一份

### 1. 工程內部版

適合：工程師、Ops、需要執行開通與排查的人。

內容包含：
- 環境準備與啟動
- Admin API 開通步驟
- LINE Webhook 設定
- Google / Meta 串接
- 驗收與 DB / API 排查
- 已知限制與風險

文件：
- [工程版 SOP](./SOP_商家導入與開通流程_工程版.md)

### 2. BD/CS 對外版

適合：BD、CS、需要帶商家完成導入與交接的人。

內容包含：
- 商家前置資訊蒐集
- 對商家的說明話術
- 商家加入 LINE 後會發生什麼
- Onboarding 預期流程與時間
- 商家日常怎麼使用 Kachu+
- 常見異常何時交工程師處理

文件：
- [BD/CS 版 SOP](./SOP_商家導入與開通流程_BDCS版.md)

## 當前產品狀態摘要

- 商家開通已改用 Admin API，不需要直接操作 DB。
- LINE `follow` event 會立即推送 onboarding 歡迎訊息。
- 商家可直接在 LINE 發 EXECUTE / CONSULT / CLARIFY 類型指令。
- 部分自然語句已補強，例如「看一下最近有沒有評論要回」、「幫我建一個 VIP 顧客標籤」。
- Google / Meta OAuth 仍未完整自助化，現階段仍需工程配合。

## 開通前需先備齊的 API / 憑證

導入前請先跟商家或內部維運確認以下資料是否已備妥：

- 系統層：`POSTGRES_PASSWORD`、`FIELD_ENCRYPTION_KEY`、`ADMIN_API_TOKEN`
- LLM：`GOOGLE_AI_API_KEY` 為主，`OPENAI_API_KEY` 為備援
- Google：`GOOGLE_OAUTH_CLIENT_ID`、`GOOGLE_OAUTH_CLIENT_SECRET`
- Tenant LINE：`LINE_CHANNEL_ID`、`LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`、`BOSS_LINE_USER_ID`
- Tenant Google Business：`account_id`、`location_id`、`access_token`、`refresh_token`（強烈建議）
- Tenant Meta：`access_token`、`fb_page_id`、`ig_user_id`、`fb_access_token`（若 Facebook / IG token 分離）

細部來源與填寫位置請看 [docs/deploy-runbook.md](./deploy-runbook.md) 與 [工程版 SOP](./SOP_商家導入與開通流程_工程版.md)。

## 建議使用方式

1. BD/CS 先依 BD/CS 版完成商家資訊蒐集與說明。
2. 工程師依工程版完成 tenant 開通與 webhook 驗證。
3. 商家加入 LINE 後，由 BD/CS 陪跑第一次 onboarding。
4. 若遇到 webhook、token、OAuth、排程異常，再回工程版排查。

---

*本文件為入口索引。具體操作請改看對應角色版本。*

