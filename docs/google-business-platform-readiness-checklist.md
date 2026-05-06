# Kachu Google Business 平台級准入 Readiness Checklist

更新日期：2026-05-06（最後更新：production 全部驗證完畢）

---

## 🔔 待追蹤事項（2026-05-06 記錄）

明天起每次開工先確認這兩件事，都完成後可刪除本區塊。

### 1. GBP API Allowlist 是否核准

- **追蹤方式**：GCP Console → Opsly 專案 → API 和服務 → mybusinessbusinessinformation → 配額與系統限制
- **判斷標準**：
  - 0 QPM → 尚未核准（繼續等）
  - 300 QPM → 核准，可開始測試串接
- **Case 編號**：`8-4117000041079`（昨天 2026-05-05 送出，預期 1–7 個工作天）
- **直達連結**：https://console.cloud.google.com/apis/api/mybusinessbusinessinformation.googleapis.com/quotas?project=opsly-492412

### 2. OAuth Verification Center 按鈕是否解鎖

- **追蹤方式**：GCP Console → Opsly 專案 → Google Auth Platform → 驗證中心
- **判斷標準**：
  - `品牌未向使用者顯示` + 按鈕 disabled → 尚未同步（繼續等）
  - `Prepare for verification` 按鈕可點 → 解鎖，立即送件
- **根因說明**：4 月設定時 brand URLs 填 `app.kachu.tw`（錯），昨天已修正為 `kachu.tw` 並儲存；Google 後台尚未重新評估品牌狀態，通常數小時到 24 小時後同步
- **直達連結**：https://console.cloud.google.com/auth/verification?project=opsly-492412

### 背景說明（兩件事的關係）

```
GBP API 核准（QPM 0→300）
  → 可開始測試 + 前幾個 pilot tenant
  → 同時推進 OAuth Verification
    → 通過後 100 人 OAuth 上限解除
      → 正式規模化
```

- **OAuth 100 人額度** = 有多少商家可完成 OAuth 授權（與 API 能不能呼叫無關）
- **GBP API QPM** = API 呼叫本身能不能成功（現在是 0，全部被擋）
- **即使商家完成 OAuth 授權，只要 QPM=0，所有 GBP 操作都會失敗**

---

## 用途

這份文件是 Kachu 對 Google Business Profile API 正式對外開放前的 readiness checklist。

它的目的是把平台級條件與 tenant 級條件切開，讓團隊知道：

- 哪些項目做到才算「平台具備申請與開放資格」
- 哪些項目只是「某個 tenant 已完成 onboarding」
- 目前到底卡在哪裡

## 狀態定義

- `[x]` 已完成，且已有實際驗證依據
- `[-]` 已部分完成，但不能視為 fully ready
- `[ ]` 尚未完成或尚無足夠證據

## 主線總表

如果現在只想回答一個問題:

`Kachu 現在到底還差什麼，才能更接近 Google 平台級准入？`

那就只看下面三欄，不要先被後面的細節分散。

### 1. 已完成且可作為 Google 審查材料

- [x] 平台最低公開網站資產已齊備
	依據：主站、隱私權頁、服務條款頁、`robots.txt`、`sitemap.xml`、GSC sitemap 提交結果，見 A-3
- [x] Google 審查敘事包已存在
	依據：[docs/google-business-review-submission-package.md](docs/google-business-review-submission-package.md)，見 A-4
- [x] Google demo script 已存在
	依據：[docs/google-business-demo-script.md](docs/google-business-demo-script.md)，見 A-4
- [x] tenant-aware OAuth 與 location 綁定技術鏈路已可內部驗證
	依據：見 A-1、B-1
- [x] 至少一個正式示範 tenant 商家頁已存在且 production 可存取
	依據：`https://kachu.tw/merchants/demo-sishixunyangtang` HTTP 200，sitemap 含此 URL，2026-05-06 preflight 通過，見 B-2、B-3
- [x] 第一份 tenant 對帳基礎資料已存在
	依據：[docs/google-business-demo-tenant-reconciliation.md](docs/google-business-demo-tenant-reconciliation.md)，見 B-2

### 2. 未完成但現在可立即補證據

- [ ] 示範 tenant 的 GBP 後台欄位逐欄取證
	這是目前最直接補強准入敘事的工作，見 B-2、F-2
- [ ] 示範 tenant 的 GBP website 欄位回填正式商家頁網址並留存證據
	這會直接補強「平台能提供正式商家落地頁」的可信度，見 B-2、F-2
- [ ] 依最新 tenant / merchant page flow 重跑一次 demo 腳本並確認可操作
	這會確保送審敘事、實際操作與目前產品狀態一致，見 F-3
- [x] production 上的 merchant page 最小交付流程已驗證
	依據：`https://kachu.tw/merchants/demo-sishixunyangtang` HTTP 200，sitemap 包含此 URL，2026-05-06 preflight 全 7 項通過，見 F-2
- [ ] 對外說法統一維持「內部驗證 / 受控 rollout」
	這是為了避免文件與產品敘事誤導 Google，見 C-4

### 3. 外部依賴，現在只能追件或等待

- [ ] Google Auth Platform 對外驗證 / 審查完成
	見 A-1
- [ ] OAuth 使用者上限限制解除或不再構成阻礙
	見 A-1
- [ ] GBP API access support case `8-4117000041079` 收到明確核准
	見 A-2
- [ ] 正式外部使用者在核准狀態下完成真實驗證
	見 A-2

## 這份清單怎麼看

從現在開始，判讀順序固定如下：

1. 先看「主線總表」三欄，確認現在是在補證據，還是在等 Google。
2. 再看 A 到 C 節，查每個結論的細節依據。
3. 若某件事不直接增加審查材料、補證據或追件，就不應占用主線注意力。

## 未完成項目的處理分類

為了避免把所有 `[ ]` 混在一起看，這份清單額外用三種分類判讀未完成項目：

- `外部依賴`：最後一步在 Google 或外部審核方手上，Kachu 無法單方面把它改成完成
- `可先準備`：現在可以把前置條件、材料、流程先補齊，但還不能誠實標記為最終完成
- `可立即補做`：Kachu 現在就可以直接投入並完成的工作項目

## A. 平台級必要條件

### A-1. Google project 與申請主體

- [x] 已有可用的 Google Cloud project 供 Kachu 使用
- [x] 已有可用的 Google OAuth client 設定與 callback 流程
- [x] 已能以內部驗證方式完成 GBP OAuth 與 location 綁定
- [ ] Google Auth Platform 對外驗證 / 審查已完成
	類型：外部依賴
	現在可做：補齊送審敘事、網站資產、審查包與 demo 流程後，正式送交 Google
- [ ] OAuth 使用者上限限制已解除或已不再構成正式開放阻礙
	類型：外部依賴
	現在可做：完成 Google Auth Platform 送審，等待 Google 更新限制狀態

### A-2. GBP API 准入

- [x] 已確認 GBP API access 是 project-level 審核
- [x] 已實際送出 GBP API allowlist / access support case `8-4117000041079`
- [ ] 已收到 Google 明確核准結果
	類型：外部依賴
	現在可做：持續追蹤 support case `8-4117000041079`，並準備補件材料
- [ ] 已確認正式外部使用者可在核准狀態下使用 Google 功能
	類型：可先準備
	現在可做：先把對外 rollout 流程、風險控管與 tenant 商家頁資產準備好，待核准後再做真實外部驗證

### A-3. 平台網站與可驗證公開資產

- [x] 主站 `kachu.tw` 可公開存取
- [x] 已有隱私權頁 `https://kachu.tw/privacy`
- [x] 已有服務條款頁 `https://kachu.tw/terms`
- [x] 已有 `robots.txt`
- [x] 已有 `sitemap.xml`
- [x] GSC domain property 可存取
- [x] sitemap 已成功提交到 GSC
- [x] GSC drilldown 已顯示 sitemap 順利處理完畢
- [ ] 主站公開內容是否足以支撐 Google 審查敘事，仍待與實際審查結果交叉驗證
	類型：可先準備
	現在可做：持續補強首頁的產品敘事、服務流程、聯絡與信任訊號；但最終是否足夠仍要看 Google 審查結果

### A-4. 申請材料與審查準備

- [x] 已有可引用的網站資產與公開政策頁
- [x] 已有實際可展示的 OAuth / location 綁定流程
- [x] 已有內部驗證用的 Google connector 流程
- [x] 是否已整理成完整對 Google 可提交的審查說明包
	依據：[docs/google-business-review-submission-package.md](docs/google-business-review-submission-package.md)
- [x] 是否已準備好若 Google 要求 demo 時的標準展示流程
	依據：[docs/google-business-demo-script.md](docs/google-business-demo-script.md)

## B. Tenant 級必要條件

### B-1. Tenant 授權鏈路

- [x] tenant-aware Google OAuth flow 已存在
- [x] 多 location 情境已支援選擇要綁定的 location
- [x] connector 會保存 `account_id`、`location_id`、`location_title`
- [ ] 正式外部 tenant onboarding 是否已開放 Google 入口
	類型：可先準備
	現在可做：先把 tenant 商家頁、欄位對帳與 rollout guardrail 補好；未核准前不應正式開放

### B-2. Tenant 商家一致性

- [-] tenant 商家名稱、電話、地址、營業時間是否已和 GBP 完整對齊
	依據：已建立第一份示範 tenant 對帳表 [docs/google-business-demo-tenant-reconciliation.md](docs/google-business-demo-tenant-reconciliation.md)，Kachu 端目標值已定；但尚未到 GBP 後台逐欄取證，因此不能視為完成
- [x] tenant 若沒有既有官網，是否已有對應正式商家頁
	依據：`https://kachu.tw/merchants/demo-sishixunyangtang`
- [-] tenant 商家頁是否與 GBP 網址欄位一致
	依據：示範 tenant 已先固定正式網址 `https://kachu.tw/merchants/demo-sishixunyangtang`，並寫入對帳表；但尚未證明該網址已實際回填到 GBP website 欄位

### B-3. Tenant 正式頁資產

- [x] 已定義共用主網域 + 子網域的產品策略
- [x] 已定義商家頁最低內容規格
- [x] 是否已實作 tenant 商家頁自動生成或管理流程
	依據：`/merchants/{merchant_slug}` 已改成讀取 `src/kachu/static/merchant_pages/*.json` 的最小資料驅動流程，dashboard 已新增 `/dashboard/api/merchant-page` 讀寫 API、tenant settings 綁定的 merchant slug，以及 `/dashboard/api/merchant-page/template` 範本生成能力；目前仍屬最小管理流程，不是完整 onboarding 自動生成產品
- [x] 是否已建立至少一個可作為正式範例的 tenant 商家頁
	依據：`https://kachu.tw/merchants/demo-sishixunyangtang`（對應 merchant data 檔 `src/kachu/static/merchant_pages/demo-sishixunyangtang.json`）

## C. 替代接入路徑評估

### C-1. 不做網站直接申請

- [x] 已確認這不是可依賴路徑
- [x] 無需再投入時間嘗試把它當主要方案
	依據：[docs/google-business-platform-access-strategy.md](docs/google-business-platform-access-strategy.md)

### C-2. 只靠 manager access

- [x] 已確認可作為少量人工營運補充
- [x] 已確認不適合作為主要產品路徑

### C-3. 只靠 service account 或平台後台代理

- [x] 已確認目前沒有可靠官方依據支持
- [x] 不應作為正式產品方案

### C-4. 內部驗證 / 受控 rollout

- [x] 這是目前合理的暫時狀態
- [x] 可作為正式核准前的過渡方案
- [ ] 不能被誤寫成已正式對外開放
	類型：可先準備
	現在可做：統一文件、對外說法與產品文案，明確維持「內部驗證 / 受控 rollout」口徑

## D. Kachu 當前判讀

截至 2026-05-06，Kachu 應該被判定為：

- `Google 技術鏈路`：已可內部驗證
- `平台網站最低公開資產`：已補齊
- `GSC / sitemap 基礎可索引性`：已建立
- `Google 平台級正式准入`：未完成
- `tenant 級正式 rollout`：未完成

因此目前最正確的狀態標記是：

- `技術上可驗證`
- `產品上未正式開放`

更直白地說：

- Google 現在不是在等 Kachu 再做更多內部工具
- Google 真正還看不到的，是外部審核結果與示範 tenant 的可取證材料
- 所以主線任務不是「繼續做功能」，而是「補證據 + 追件」

## E. Go / No-Go 判準

### 可以進入對外 rollout 的最低條件

以下條件若沒有全部成立，不應把 Google 功能當成正式 GA 功能對外銷售：

- [ ] Google Auth Platform 對外驗證或審查完成
	類型：外部依賴
- [ ] GBP API access 已收到明確核准
	類型：外部依賴
- [ ] 平台網站與公開政策頁已齊備
	類型：可先準備
- [ ] tenant onboarding 流程已能穩定支援正式授權
	類型：可先準備
- [ ] tenant 正式頁策略已至少有一個可實際交付的產品路徑
	類型：可立即補做

### 目前是否達到 Go 條件

- [ ] 尚未達到

原因：

1. 平台級 Google 審核尚未完成
2. OAuth 對外限制訊號仍存在
3. tenant 商家頁已有最小管理流程，但仍未完成結構化編輯、production 驗證與 GBP 欄位取證

## F. 接下來最該做的事

### 最高優先：直接影響准入的事

1. 追蹤 Google support case `8-4117000041079` 結果
2. 完成 Google Auth Platform 必要審查動作與資料整理
3. 示範 tenant 的 GBP 後台欄位逐欄取證
4. 示範 tenant 的 GBP website 欄位回填正式商家頁網址並留存證據
5. 依最新 tenant / merchant page flow 重跑一次 demo script 並確認可操作

### 第二優先：只保留會支撐證據的項目

#### F-1. 平台敘事與公開資產

- [ ] 主站公開內容是否已足以支撐 Google 審查敘事
- [ ] 對外說法是否已統一維持「內部驗證 / 受控 rollout」

#### F-2. Tenant 商家頁交付與證據

- [x] tenant settings 已能綁定 merchant slug
- [x] dashboard 已能產生 merchant page 範本
- [x] dashboard 已能載入與儲存 merchant page JSON
- [x] merchant page 最小交付流程已在 production 部署並以正式網址驗證
	依據：`https://kachu.tw/merchants/demo-sishixunyangtang` HTTP 200，2026-05-06 preflight 通過

#### F-3. Tenant 與 GBP 欄位對帳

- [x] 已建立示範 tenant 對帳表
- [ ] 示範 tenant 的 GBP 後台欄位是否已逐欄取證
- [ ] 示範 tenant 的 GBP website 欄位是否已回填正式商家頁網址並留存證據

#### F-4. Google Demo 準備

- [x] 已準備標準 demo script
- [ ] 是否已依最新 merchant page / tenant flow 重跑一次 demo 腳本並確認可操作

### 降級處理：不是目前主線的事

以下事項可以做，但不應再當成現在的主線：

- dashboard 的進一步產品化美化
- merchant page 後台做成完整 CMS
- 與准入證據無直接關聯的內部管理優化

### 不該誤判的事

1. 不要因為 OAuth 技術能跑，就把 Google 功能寫成已正式開放
2. 不要把 tenant 頁面需求誤解成每個客戶都必須買獨立網域和主機
3. 不要把 manager access 或 service account 幻想成可正式取代平台級准入

## G. 相關文件

- [docs/google-business-review-submission-package.md](docs/google-business-review-submission-package.md)
- [docs/google-business-demo-script.md](docs/google-business-demo-script.md)
- [docs/google-business-platform-access-strategy.md](docs/google-business-platform-access-strategy.md)
- [docs/google-business-website-spec.md](docs/google-business-website-spec.md)
- [docs/kachu-full-validation-checklist.md](docs/kachu-full-validation-checklist.md)