# Kachu Google Business 審查提交包

更新日期：2026-05-05

## 用途

這份文件是 Kachu 目前可直接整理給 Google 的審查說明包第一版，用來支撐以下情境：

- Google Auth Platform 或相關審查要求補件
- GBP API access support case 要求提供產品與網站說明
- Google 要求說明 Kachu 為什麼需要 GBP 功能，以及實際如何使用

這份文件的原則是：

- 只陳述目前已成立的事實
- 不把「內部驗證可跑」誤寫成「正式對外開放」
- 讓 Google 能快速理解 Kachu 是什麼、做什麼、為什麼需要 GBP access

## 一、申請主體摘要

- 產品名稱：Kachu
- 官方網站：https://kachu.tw/
- 隱私權政策：https://kachu.tw/privacy
- 服務條款：https://kachu.tw/terms
- Sitemap：https://kachu.tw/sitemap.xml
- Search Console domain property：`sc-domain:kachu.tw`
- GBP API support case：`8-4117000041079`

## 二、Kachu 是什麼

Kachu 是一個給中小型商家使用的 AI 數位幕僚產品，主要透過 LINE 與商家互動，協助處理：

- 商家內容草稿產生
- Google 商家動態草稿與後續發布流程
- Google 評論回覆建議
- 商家營運資料整理與工作流輔助

Kachu 的產品定位不是一般消費者工具，而是協助商家或商家經營者管理自己的對外數位營運內容。

## 三、為什麼 Kachu 需要 Google Business Profile access

Kachu 申請 Google Business Profile access 的目的，是讓已授權的商家能透過 Kachu 完成與 Google Business Profile 相關的營運協作，例如：

- 草擬 Google 商家貼文內容
- 協助商家回覆評論
- 在商家明確授權前提下，讓 Kachu 代表商家執行對應操作

Kachu 不應被描述為未經授權自動操作任意商家資料的系統；它是以 tenant 授權、location 綁定與受控流程為前提的商家營運工具。

## 四、Kachu 目前公開可驗證的網站資產

目前可公開存取、可供 Google 審查的網站資產如下：

- 首頁：https://kachu.tw/
- 隱私權頁：https://kachu.tw/privacy
- 服務條款頁：https://kachu.tw/terms
- robots.txt：https://kachu.tw/robots.txt
- sitemap.xml：https://kachu.tw/sitemap.xml
- 示範商家頁：https://kachu.tw/merchants/demo-sishixunyangtang

這些頁面已在 production 上線，且 sitemap 已送進 GSC 並顯示處理成功（2026-05-06 preflight 全部 HTTP 200 確認）。

示範商家頁說明 Kachu 的商家落地頁策略：對沒有既有網站的商家，Kachu 採共用主網域加路徑的標準化商家頁策略，該頁面即為此策略的正式範例。

## 五、Kachu 目前已存在的實際流程

截至 2026-05-05，Kachu 已可完成以下內部驗證流程：

1. 商家透過 tenant-aware Google OAuth flow 完成授權
2. 若 Google 帳號底下有多個商家位置，Kachu 可要求使用者選擇正確 location
3. Kachu 會保存 `account_id`、`location_id`、`location_title`
4. 後續 Google 商家相關流程只應綁定到被選定的 location

目前這些流程已達到內部驗證可運作，但尚未把 Google 功能標示為正式對外 GA 開放。

## 六、Kachu 對外開放邊界的真實狀態

以下說法是目前應維持的真實狀態：

- Kachu 已完成 Google 相關技術鏈路的內部驗證
- Kachu 已補齊平台級最低公開網站資產
- Kachu 尚未聲稱 GBP API access 已最終核准
- Kachu 尚未聲稱 Google 功能已對不特定外部使用者正式全面開放

這個表述應在所有審查與對外材料中保持一致。

## 七、建議提供給 Google 的補充證據

若 Google 要求更完整審查材料，建議一起準備下列證據：

1. 首頁、隱私權、服務條款的截圖
2. Search Console sitemap 成功處理畫面截圖
3. Google OAuth 成功頁截圖
4. 多 location 選擇頁截圖
5. connector 成功保存 `account_id` / `location_id` 的證據截圖
6. 內部 demo 流程說明

## 八、Google 若詢問「誰會使用這個功能」時的標準回答

建議回答方向：

- 使用者是 Kachu 的商家客戶或其授權經營者
- 每個 tenant 必須先完成授權，Kachu 才會存取對應商家資料
- Kachu 不會在未授權的情況下代表商家操作 Google Business Profile

## 九、Google 若詢問「網站與商家頁如何對應」時的標準回答

建議回答方向：

- Kachu 本身已有平台級正式網站資產可供審查
- 對沒有既有網站的商家，Kachu 採共用主網域加路徑的標準化商家頁策略（格式：`https://kachu.tw/merchants/{slug}`）
- 示範範例：https://kachu.tw/merchants/demo-sishixunyangtang
- tenant 商家頁的定位是正式商家資產與 onboarding 配套，而不是每個客戶都必須自行重建一個完整獨立官網
- 商家可透過 Kachu Dashboard 管理其商家頁內容，並將該 URL 填入 GBP website 欄位

## 十、現階段不應提交的錯誤說法

以下內容不應出現在提交材料中：

- 「Kachu 的 Google 功能已正式全面開放」
- 「每個 tenant 一定都已經有正式商家頁」
- 「Google 已經明確核准 Kachu 的 GBP API access」
- 「Kachu 不需要商家授權就能操作商家資料」

## 十一、提交前最後檢查

在實際拿這份材料去回 Google 之前，應再次確認：

1. 所有公開連結仍回 200
2. 首頁文案沒有誤導成已正式全面開放 Google 功能
3. support case 編號、產品名稱與網站網址一致
4. demo 流程與提交材料說法一致

## 相關文件

- [docs/google-business-demo-script.md](docs/google-business-demo-script.md)
- [docs/google-business-platform-access-strategy.md](docs/google-business-platform-access-strategy.md)
- [docs/google-business-platform-readiness-checklist.md](docs/google-business-platform-readiness-checklist.md)
- [docs/google-business-website-spec.md](docs/google-business-website-spec.md)