# Kachu v2 — 憑證輪替 Checklist

> 更新日期：2026-04-28  
> 適用時機：首次部署前，或懷疑任何 key 曾洩漏時  
> 完成後：將所有值填入 `.env.prod`，替換對應的 `REPLACE_WITH_*` 欄位

---

## 本機產生的 key（不需要登入任何外部服務）

### ✅ KEY-01 — `SECRET_KEY`

```powershell
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

- 複製輸出（64 個十六進位字元）
- 填入 `.env.prod` 的 `SECRET_KEY=`
- ⬜ 完成

---

### ✅ KEY-02 — `TOKEN_ENCRYPTION_KEY`

```powershell
.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- 複製輸出（44 個字元的 base64url 字串）
- 填入 `.env.prod` 的 `TOKEN_ENCRYPTION_KEY=`
- ⬜ 完成

---

### ✅ KEY-03 — `POSTGRES_PASSWORD`

```powershell
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(24))"
```

- 複製輸出（32 個字元，只含 URL-safe 字元，適合放進 DB URL）
- 同時填入 `.env.prod` 的兩個地方：
  - `POSTGRES_PASSWORD=<貼上>`
  - `DATABASE_URL=postgresql+psycopg://kachu:<貼上>@postgres:5432/kachu`
- ⚠️ 兩個地方的密碼**必須完全一致**
- ⬜ 完成

---

## LINE Developer Console

> 網址：https://developers.line.biz/  
> 路徑：進入對應 Channel → **Messaging API** 頁籤

### ✅ KEY-04 — `LINE_CHANNEL_ACCESS_TOKEN`

1. 在 Messaging API 頁籤找到 **Channel access token** 區塊
2. 點擊 **Issue** 重新產生長效 token
3. 複製新 token
4. 填入 `.env.prod` 的 `LINE_CHANNEL_ACCESS_TOKEN=`
5. ⬜ 完成

---

### ✅ KEY-05 — `LINE_CHANNEL_SECRET`

1. 切換到 **Basic settings** 頁籤
2. 找到 **Channel secret** 欄位，點擊 **Reissue** 重新產生
3. 複製新 secret
4. 填入 `.env.prod` 的 `LINE_CHANNEL_SECRET=`
5. ⬜ 完成

---

### ✅ KEY-06 — `LINE_BOSS_USER_ID`

這不是 secret，是你自己的 LINE user ID（老闆帳號）。

取得方式：
1. 啟動服務後，用你的個人 LINE 帳號傳訊息給 bot
2. 在 `docker compose logs kachu` 中找到 `source.userId` 欄位
3. 或在 LINE Developers Console → Messaging API → **Your user ID** 查看

填入 `.env.prod` 的 `LINE_BOSS_USER_ID=`
⬜ 完成

---

## Google AI Studio（Gemini）

> 網址：https://aistudio.google.com/

### ✅ KEY-07 — `GOOGLE_AI_API_KEY`

1. 進入 **Get API key** → **Create API key**
2. 建議選擇 project `opsly-492412`（與 service account 同 project）
3. 複製新 key
4. 填入 `.env.prod` 的 `GOOGLE_AI_API_KEY=`
5. （選擇性）撤銷 `.env` 裡的舊 key：在 API Keys 列表點選舊 key → **Delete**
6. ⬜ 完成

---

## OpenAI Platform（Embeddings）

> 網址：https://platform.openai.com/api-keys

### ✅ KEY-08 — `OPENAI_API_KEY`

1. 點擊 **+ Create new secret key**
2. 命名為 `kachu-prod`（方便識別）
3. 複製新 key（只顯示一次）
4. 填入 `.env.prod` 的 `OPENAI_API_KEY=`
5. 撤銷舊 key：在列表找到舊 key → 點選垃圾桶圖示 → Revoke
6. ⬜ 完成

---

## Google Cloud Console（OAuth 2.0）

> 網址：https://console.cloud.google.com/  
> 路徑：APIs & Services → Credentials → OAuth 2.0 Client IDs

### ✅ KEY-09 — `GOOGLE_OAUTH_CLIENT_SECRET`

1. 找到 Client ID `764850881411-...` 對應的 OAuth 2.0 Client
2. 點擊進入 → **Reset Secret**（或 **Download JSON** 取得新 secret）
3. 複製新 Client Secret
4. 填入 `.env.prod` 的 `GOOGLE_OAUTH_CLIENT_SECRET=`
5. 確認 **Authorized redirect URIs** 包含 `https://app.kachu.tw/auth/google/callback`
6. ⬜ 完成

---

## Google Cloud Console（Service Account）

> 路徑：IAM & Admin → Service Accounts → `contentflow-gsc@opsly-492412.iam.gserviceaccount.com`

### ✅ KEY-10 — `credentials/google-service-account.json`

這個檔案的 private key 已存在於工作區，需確認 service account 仍有效：

1. 在 GCP Console 確認此 service account 狀態為 **Enabled**
2. 確認以下 API 已啟用（APIs & Services → Library）：
   - **Google Business Profile API**
   - **Google My Business API**（若有使用）
3. 確認 service account 有適當的角色（IAM → 查看此 SA 的權限）
4. 若要輪替 key：Keys → **Add Key → Create new key → JSON** → 下載替換現有的 `credentials/google-service-account.json`
5. ⬜ 確認 service account 有效

---

## Meta for Developers（Facebook / Instagram）

> 網址：https://developers.facebook.com/  
> 路徑：My Apps → 選擇對應 App → App Settings → Basic

### ✅ KEY-11 — `META_APP_SECRET`

> 若目前不打算使用 Meta 功能，可在 `.env.prod` 中確認 `FEATURE_META=False` 並跳過此步驟。

1. 在 App Settings → Basic 找到 **App Secret** 欄位
2. 點擊 **Show** 查看，或點擊 **Reset** 輪替
3. 複製 App Secret
4. 填入 `.env.prod` 的 `META_APP_SECRET=`
5. ⬜ 完成 或 ⬜ 跳過（FEATURE_META=False）

---

## 填入 `.env.prod` 後的最終確認

完成所有輪替後，確認 `.env.prod` 中沒有任何 `REPLACE_WITH_*` 字串：

```powershell
Select-String -Path .env.prod -Pattern "REPLACE_WITH"
```

**預期**：無任何輸出（代表所有欄位都已填妥）  
⬜ 確認完成

---

## Google Business Profile 帳號資訊補充

以下兩個欄位目前為空，需要填入：

| 欄位 | 如何取得 |
|---|---|
| `GOOGLE_BUSINESS_ACCOUNT_ID` | Google Business Profile Manager → 帳號 URL 中的數字 ID |
| `GOOGLE_BUSINESS_LOCATION_ID` | 同上 → 選擇門市 → URL 中的 location ID |

⬜ 填入 `GOOGLE_BUSINESS_ACCOUNT_ID=`  
⬜ 填入 `GOOGLE_BUSINESS_LOCATION_ID=`

---

## 完成狀態總覽

| Key | 欄位 | 狀態 |
|---|---|---|
| KEY-01 | `SECRET_KEY` | ⬜ |
| KEY-02 | `TOKEN_ENCRYPTION_KEY` | ⬜ |
| KEY-03 | `POSTGRES_PASSWORD` + `DATABASE_URL` | ⬜ |
| KEY-04 | `LINE_CHANNEL_ACCESS_TOKEN` | ⬜ |
| KEY-05 | `LINE_CHANNEL_SECRET` | ⬜ |
| KEY-06 | `LINE_BOSS_USER_ID` | ⬜ |
| KEY-07 | `GOOGLE_AI_API_KEY` | ⬜ |
| KEY-08 | `OPENAI_API_KEY` | ⬜ |
| KEY-09 | `GOOGLE_OAUTH_CLIENT_SECRET` | ⬜ |
| KEY-10 | Google service account 確認有效 | ⬜ |
| KEY-11 | `META_APP_SECRET`（或確認跳過） | ⬜ |
| — | `GOOGLE_BUSINESS_ACCOUNT_ID` | ⬜ |
| — | `GOOGLE_BUSINESS_LOCATION_ID` | ⬜ |
| — | `.env.prod` 無 `REPLACE_WITH_*` 殘留 | ⬜ |
