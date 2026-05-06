# Kachu Google Business Demo Script

更新日期：2026-05-06

## 用途

這份文件定義 Kachu 面對 Google 審查或補件要求時，可以使用的標準 demo 流程。

目標不是炫技，而是讓 Google 清楚看到：

- Kachu 是真實存在的產品
- Kachu 有公開網站與基本政策頁
- Kachu 的 Google 流程建立在商家授權與 location 綁定之上
- Kachu 目前維持內部驗證 / 受控 rollout，不誤稱已全面正式開放

## Demo 核心原則

整場 demo 都應維持以下口徑：

1. Kachu 是商家營運輔助產品
2. 商家必須先授權，Kachu 才能綁定商家資料
3. 若帳號底下有多個 location，使用者必須主動選定正確商家位置
4. 目前 Google 功能處於內部驗證或受控 rollout 階段，不應誤導成已全面公開

## Demo 前準備

開始前請先確認：

1. https://kachu.tw/ 可正常開啟
2. https://kachu.tw/privacy 可正常開啟
3. https://kachu.tw/terms 可正常開啟
4. https://kachu.tw/merchants/demo-sishixunyangtang 可正常開啟（展示示範商家頁）
5. Google 測試帳號可正常登入
6. 該測試帳號底下至少有一個可用 GBP location
7. 若要展示多 location 選擇流程，請使用底下有多個 location 的測試帳號

## Demo 流程

### Step 1. 展示 Kachu 主站

要展示的頁面：

- 首頁：https://kachu.tw/
- 隱私權：https://kachu.tw/privacy
- 服務條款：https://kachu.tw/terms

要說的重點：

- Kachu 是提供給商家的 AI 數位幕僚產品
- 網站可公開存取，不是臨時不可驗證頁面
- 已有基本政策頁與可索引資產

### Step 2. 說明 Kachu 為什麼需要 Google Business Profile

要說的重點：

- Kachu 會協助商家處理 Google 商家相關營運內容
- 功能包括商家貼文草稿、評論回覆輔助與後續受控操作流程
- 這些功能都建立在商家授權與 tenant 綁定前提上

### Step 3. 展示 Google OAuth connect 入口

展示重點：

- Kachu 有 tenant-aware 的 Google connect 流程
- connect URL 會帶 `tenant_id`
- 這不是沒有上下文的共用 token 流程

要說的重點：

- 每個 tenant 都要先完成自己的 Google 授權
- Kachu 不會在未授權下直接操作商家資料

### Step 4. 完成授權後展示 location 綁定

若示範帳號有多個 location，請展示 location selection 頁。

要說的重點：

- 若 Google 帳號底下有多個商家位置，使用者必須選擇正確 location
- Kachu 之後保存 `account_id`、`location_id`、`location_title`
- 後續流程只會綁到被使用者選定的那間店

### Step 5. 展示成功頁與內部驗證口徑

展示重點：

- Google 授權成功頁
- location 已選定的結果

要說的重點：

- 目前這個流程已可做內部驗證
- 但 Kachu 不把它誤寫成已全面正式對外開放

### Step 6. 展示示範 tenant 商家頁

展示頁面：

- 示範商家頁：https://kachu.tw/merchants/demo-sishixunyangtang

要說的重點：

- 這是 Kachu 商家頁策略的實際範例：對沒有既有網站的商家，提供一張可公開存取、可填入 GBP website 欄位的正式送達頁
- 商家頁 URL 格式定義為 `https://kachu.tw/merchants/{merchant_slug}`
- merchant slug 由 Kachu Dashboard 進行 tenant settings 綁定，商家可自行管理内容
- 這個頁面已即時在 production 上線，並已納入 sitemap.xml

### Step 7. 說明 tenant 商家頁策略

要說的重點：

- Kachu 平台本身已具備平台級正式網站資產
- 對沒有既有網站的商家，Kachu 採共用主網域加路徑（`/merchants/{slug}`）的標準化商家頁策略
- 這是正式資產與 onboarding 配套，不是每個客戶都重新建完整官網

### Step 8. 以真實邊界收尾

最後必須清楚說：

- Kachu 已完成技術鏈路的內部驗證
- 已補齊平台級最低公開資產
- 目前正在等待平台級正式准入結果

## Google 若追問時的標準回答

### 如果 Google 問「這是不是對外公開給所有人用？」

建議回答：

目前 Google 功能仍在受控 rollout / internal validation 階段。Kachu 不會把尚未核准完成的功能誤標成全面正式公開。

### 如果 Google 問「誰授權你們存取商家資料？」

建議回答：

是由商家或其授權經營者透過 Kachu 的 Google OAuth flow 明確授權，之後再由使用者選定對應商家 location。

### 如果 Google 問「沒有網站的商家怎麼辦？」

建議回答：

Kachu 平台本身已有正式網站資產；對沒有既有網站的商家，Kachu 提供共用主網域加路徑的標準化商家頁，格式為 `https://kachu.tw/merchants/{slug}`。該商家頁即為正式商家資產，可填入 GBP website 欄位。

可展示範例：https://kachu.tw/merchants/demo-sishixunyangtang

## Demo 禁忌

demo 時不要說：

- 「我們已經完全通過 Google 審核」
- 「現在所有外部客戶都能直接正式使用 Google 功能」
- 「不需要授權也能綁定商家」

## Demo 後續補件清單

若 Google 要求把 demo 補成書面材料，應附上：

1. 主站與政策頁網址
2. OAuth 流程截圖
3. location selection 截圖
4. 成功頁截圖
5. 示範商家頁截圖（https://kachu.tw/merchants/demo-sishixunyangtang）
6. 審查提交包文件

## 相關文件

- [docs/google-business-review-submission-package.md](docs/google-business-review-submission-package.md)
- [docs/google-business-platform-readiness-checklist.md](docs/google-business-platform-readiness-checklist.md)
- [docs/google-business-platform-access-strategy.md](docs/google-business-platform-access-strategy.md)