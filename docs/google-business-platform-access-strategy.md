# Kachu Google Business 平台級准入策略

更新日期：2026-05-05

## 目的

這份文件說明 Kachu 所謂的「平台級准入資格」到底是什麼、它和單一 tenant onboarding 的差別、目前有哪些官方已確認事項，以及 Kachu 應採取的產品與營運策略。

這份文件的重點不是教單一商家怎麼去開 Google 商家，而是回答：

- Kachu 要先滿足哪些條件，Google 才有可能讓這個平台正式用 GBP API 服務客戶。
- 這些條件哪些是平台固定成本，哪些才是每個 tenant 的後續成本。
- 有沒有替代接入路徑，可以降低或繞過目前的網站與審核門檻。

## 核心結論

截至 2026-05-05，Kachu 對 Google Business Profile API 的現實結論如下：

1. 准入審核是平台級，不是 tenant 級。
2. Google 真正在審的是 Kachu 所用的 Google Cloud project、OAuth app 與申請主體，不是每個 downstream tenant 各自重審一次。
3. 但 tenant 仍然需要各自完成商家授權、位置綁定，以及資料一致性的 onboarding。
4. `Company website` 仍應視為高可信的申請前置條件，目前沒有證據顯示可以正式繞開。
5. 「共用主網域 + tenant 子網域」是目前最合理的產品化策略，因為它把網站成本收斂成平台固定資產，而不是每個客戶都單獨買網域與主機。
6. 目前沒有可靠、官方支持、又可規模化的替代路徑，能讓 Kachu 在未過平台級審核前，直接把 Google 商家功能對外正式開放。

## 什麼是平台級准入資格

這裡的「平台級准入資格」，指的是 Kachu 這個產品要以第三方平台身份，正式提供 Google Business Profile API 功能之前，必須先滿足的一組條件。

它和 tenant onboarding 的差別如下：

### 平台級

- Google Cloud project 是否具備申請資格。
- OAuth app 是否處於可送審狀態。
- 申請主體是否有真實網站、合法用途、可驗證身份與可供審查的產品流程。
- 是否已送出 Google 相關審查並取得正式核准。

### Tenant 級

- 該 tenant 是否授權自己的 Google 帳號。
- 該 tenant 是否選定正確的 GBP location。
- 該 tenant 的商家資訊、網址、品牌資料是否與實際營運內容一致。

簡單說：

- 平台級是在解決「Kachu 有沒有資格做這門生意」。
- Tenant 級是在解決「某個客戶能不能開始用這項功能」。

## 官方已確認事項

根據目前已查到的 Google 官方文件、Search Console/Google Auth Platform 實查結果，以及已送出的 support case，可視為已確認的事項如下：

1. GBP API access 的核准單位是 Google Cloud project 層級。
2. 申請者需要有真實且合法的商業用途。
3. 申請者需要管理一個已驗證且啟用超過 60 天的 Google Business Profile。
4. 申請流程中 `Company website` 被視為必填欄位。
5. 第三方平台在專案審核通過後，可以透過 OAuth 或 manager access 代表客戶管理商家檔案。
6. Google 可能要求提供 demo、操作說明或額外審查資料。
7. 目前 `Opsly` 專案在 Google Auth Platform 仍顯示「應用程式需要經過驗證，資訊設定完畢之後，請將應用程式送交審查」，而且 OAuth 使用者上限仍在生效，不能視為已完成正式對外開放資格。

## 官方沒有明文保證的事項

以下幾點目前不能被說成「Google 官方已明講」：

1. 每個 downstream tenant 都必須各自擁有獨立網站。
2. 共用主網域加子網域頁面一定會被 Google 接受。
3. 可以只靠 service account 或純後台權限，就完全取代使用者 OAuth。
4. 可以在未完成平台級審核的前提下，把產品功能正式開放給不特定外部使用者。

因此，Kachu 的對外與對內說法都應保守：

- 平台級網站與審核是高可信必要條件。
- tenant 級正式頁是產品策略與風險緩解手段。
- 但目前不應宣稱 Google 官方要求每個 tenant 都各自重跑一次網站准入審查。

## Kachu 面對的真實門檻

把 Kachu 的 Google 接入拆開看，主要有四個門檻。

### 1. Google Auth / OAuth 對外可用性

這是「你的 OAuth app 能不能正式給外部使用者登入與授權」的門檻。

目前風險信號：

- Google Auth Platform 仍顯示需要送審。
- OAuth user cap 仍在。

這代表目前不能把 Google 功能當成已正式開放。

### 2. GBP API project access

這是「Google 會不會讓這個專案正式使用 GBP API」的門檻。

目前狀態：

- 已實際送出 support case `8-4117000041079`。
- 尚未收到已核准的最終結果。

### 3. 平台網站與可驗證產品外觀

這是「Google 看起來會不會把 Kachu 當成一個真實、可識別、可供審查的平台」的門檻。

目前已補上的資產：

- `https://kachu.tw/privacy`
- `https://kachu.tw/terms`
- `https://kachu.tw/robots.txt`
- `https://kachu.tw/sitemap.xml`
- GSC 已提交 sitemap，且已順利處理完畢

但這還只是最低公開資產，不等於整體審核已完成。

### 4. Tenant onboarding 與商家一致性

即使平台級准入過了，每個 tenant 仍要滿足實作層面的接入條件：

- Google OAuth 成功
- location 選定成功
- 商家資訊一致
- 若 Kachu 提供 tenant 頁面，該頁面內容與 GBP 要能對得上

## 平台固定成本 vs tenant 可變成本

這是產品判斷最重要的切法。

### 平台固定成本

這些成本只要做一次，之後可以攤給所有 tenant：

1. Google Cloud project 與 OAuth app 建置
2. Google Auth Platform 送審與對應資料準備
3. GBP API access 申請與 support 溝通
4. Kachu 官網、隱私權、條款、sitemap、robots 等公開資產
5. Kachu 標準化商家頁系統與 shared-domain / subdomain 基礎設施

### Tenant 可變成本

這些事情每個 tenant 都還是要各自做：

1. 授權自己的 Google 帳號
2. 選定自己的商家 location
3. 對齊品牌資料、地址、電話、營業時間
4. 若沒有既有正式網站，補齊對外商家頁內容

Kachu 現在應該把網站這件事理解為：

- 平台至少要先有自己的可驗證網站資產
- tenant 級頁面則是後續 onboarding 與信任一致性資產
- 但不應把它描述成「每來一個客戶就一定要重新從零搭一個獨立官網」

## 替代接入路徑評估

以下是目前看過後，較常被直覺想到的替代路徑，以及 Kachu 的判斷。

### 路徑 A：完全不做網站，直接申請 API

判斷：不可依賴。

原因：

- `Company website` 在申請流程中是明顯必填。
- 目前沒有可信證據顯示可以正式略過。

### 路徑 B：只用 Kachu 主站，不做 tenant 子頁

判斷：平台級可行，但產品上不夠穩。

原因：

- 作為平台申請主體，Kachu 主站本身很重要。
- 但對沒有既有網站的 tenant 而言，若完全沒有對外正式頁，後續商家一致性、品牌識別與營運風險會偏高。

因此這不是不能做，而是不建議只停在這裡。

### 路徑 C：改用 manager access，避免每個 tenant 做 OAuth

判斷：可作為少量人工營運補充，不適合作為產品預設主路徑。

原因：

- 官方允許第三方透過 manager access 管理商家。
- 但這會增加人工操作、邀請流程與營運複雜度。
- 對 SaaS 產品來說，不如 tenant 自己 OAuth 來得可規模化。

### 路徑 D：用 service account 或後台 token 直接代理所有 tenant

判斷：目前沒有官方支撐，不應採用。

原因：

- 目前沒有看到 Google 提供明確的「第三方多租戶 SaaS 可直接以 service account 取代 tenant 授權」路徑。
- 若硬做，很可能違反產品與安全邊界。

### 路徑 E：先手動營運，不正式開放 Google 功能

判斷：可以作為過渡方案，但不是最終產品答案。

原因：

- 在平台級審核尚未完成前，內部驗證與少量人工流程是合理的。
- 但它無法等同於「產品已可正式對外提供 Google 功能」。

## 建議策略

Kachu 現階段應採取以下策略。

### 1. 明確區分三個階段

#### 階段一：平台准入準備

- 補齊官網公開資產
- 補齊政策頁與可索引性
- 補齊商家頁產品方案
- 準備 Google 審查與 demo 說明

#### 階段二：平台級核准

- 完成 Google Auth Platform 相關審查
- 完成 GBP API access 核准
- 確認不再受目前對外限制影響

#### 階段三：tenant rollout

- 開放 tenant OAuth
- 開放 location 綁定
- 開放 tenant 商家頁與正式 onboarding

### 2. 對產品外部承諾保持保守

在平台級核准完成前，不應對外說：

- Google 功能已正式可用
- 可以對所有客戶穩定開放
- 審核一定已過

### 3. 把商家頁產品化，而不是臨時補件

tenant 頁面不應被當成一次性送審素材，而應是：

- onboarding 標準資產
- SEO 資產
- 品牌一致性資產
- 後續多 connector 共用的正式網址資產

### 4. 把人工路徑留作 fallback，而不是主路徑

manager access、手動營運、內部測試流程都可以存在，但不應拿來當正式產品假設。

## 當前 Kachu 判讀

截至 2026-05-05，Kachu 應被判定為：

- Google 串接技術鏈路已能做內部驗證
- 平台公開網站最低資產已補上
- GSC 與 sitemap 已開始建立基本索引條件
- 但平台級正式准入尚未完成
- 因此不能把 Google 功能標示為 fully launched

## 對產品結論的最終表述

最準確的產品結論應該是：

1. Kachu 目前最大的 Google 門檻是平台級准入，不是 tenant 級 API 技術問題。
2. 這個門檻本質上是平台固定成本，應由 Kachu 承擔並產品化，而不是把它理解成每個客戶都各自重新買一套網站與主機。
3. tenant 級正式頁仍然值得做，但它應被定位成標準化商家資產，而不是每個客戶的獨立客製官網專案。
4. 在 Google 平台級核准完成前，任何對外 Google 功能都應維持內部驗證或受控 rollout 的說法。

## 相關文件

- `docs/google-business-website-spec.md`
- `docs/google-business-platform-readiness-checklist.md`