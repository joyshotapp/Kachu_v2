# Kachu v2 — Beta 驗收 Smoke Test Checklist

> 適用時機：每次首次部署或重大更新後，在邀請真實企業用戶前必須全部通過  
> 更新日期：2026-04-28  
> 預估時間：60–90 分鐘  
> 前置條件：服務已依 `deploy-runbook.md` 正常啟動，所有容器狀態 healthy

---

## 測試環境

| 項目 | 說明 |
|---|---|
| 測試用 LINE 帳號 | 建議用你自己的個人 LINE 帳號加 Kachu bot 為好友 |
| 測試用 Boss User ID | `.env.prod` 的 `LINE_BOSS_USER_ID`，設為你的 LINE user ID |
| 外部可連線 URL | `https://app.kachu.tw` |

---

## 第零關：自動化回歸測試（先跑，全過才繼續）

> 這一關不需要服務跑起來，純本機執行。所有 mock-based unit test 全通過，才進行後面的手動驗收。

```bash
cd c:\Users\User\Desktop\Kachu-v2
.venv\Scripts\python.exe -m pytest tests/ -v --tb=short
```

**預期**：所有測試通過，0 failed  
**失敗處理**：修到全過再繼續，不要帶著已知失敗的測試去做手動驗收

各測試檔與驗收項目對應關係：

| pytest 檔案 | 對應手動驗收項目 |
|---|---|
| `test_line_webhook_resilience.py` | T-04、T-05（LINE webhook / retry） |
| `test_security_guards.py` | T-13、T-14（偽造 webhook 應被拒） |
| `test_phase6_audit.py` | T-12（Dashboard audit） |
| `test_phase2_workflows.py` | T-06（intent routing）、T-09（知識更新） |
| `test_photo_content_e2e.py` | T-07（圖片分析） |
| `test_edit_session_to_publish.py` | T-08（內容生成 → 審批） |
| `test_memory.py`、`test_phase4_policy.py` | T-06 的後端支撐 |

---

## 第一關：基礎服務健康

### T-01 — Health endpoint

```bash
curl -sf https://app.kachu.tw/health
```

**預期**：`{"status":"ok","service":"kachu"}`  
**失敗處理**：`docker compose logs kachu --tail=50` 查看啟動錯誤

---

### T-02 — 資料庫 migration 狀態

```bash
docker compose -f docker-compose.prod.yml exec kachu alembic current
```

**預期**：顯示 `20260430_0002 (head)`  
**失敗處理**：執行 `alembic upgrade head` 後重確認

---

### T-03 — AgentOS 健康

```bash
curl -sf http://<主機IP>:8000/health  # 若 port 未對外開放，從主機內網確認
# 或
docker compose -f docker-compose.prod.yml exec kachu \
  curl -sf http://agentos:8000/health
```

**預期**：200 OK，或類似健康回應  
**失敗處理**：`docker compose logs agentos --tail=50`

---

## 第二關：LINE Webhook 連通

### T-04 — LINE Webhook Verify

1. 前往 LINE Developers Console → Messaging API 頁籤
2. Webhook URL 欄位顯示 `https://app.kachu.tw/webhooks/line`
3. 點擊 **Verify** 按鈕

**預期**：顯示綠色「Success」或「200 OK」  
**失敗處理**：確認 nginx 443 正常、TLS 憑證有效、`/webhooks/line` endpoint 可達

---

### T-05 — LINE 加好友 + 自動歡迎回應

操作：用測試 LINE 帳號加 Kachu bot 為好友（或傳送一則 `follow` 事件）

**預期**：收到歡迎訊息，或至少不報錯  
**在 log 確認**：`docker compose logs kachu --tail=30 | grep "follow\|webhook"`

---

## 第三關：核心 LINE 指令流程

> 以下測試請從你的 LINE 帳號（設為 `LINE_BOSS_USER_ID` 的帳號）傳訊息給 bot

### T-06 — 傳送一般文字訊息（intent routing）

傳送：`你好`

**預期**：bot 有回應（任何回應均可），不應看到「系統發生錯誤」類的訊息  
**在 log 確認**：intent router 有被觸發

---

### T-07 — 傳送圖片（照片分析流程）

傳送：一張店面或產品照片

**預期**：bot 回覆照片分析摘要，或至少說明正在處理中  
**注意**：若 `GOOGLE_AI_API_KEY` 無效，會收到降級訊息，這是預期行為  
**在 log 確認**：`docker compose logs kachu --tail=50 | grep "photo\|image"`

---

### T-08 — 傳送內容生成指令

傳送：`幫我寫一篇 IG 貼文，主題是新品上市`

**預期**：bot 回覆草稿或進入審批流程  
**在 log 確認**：`generate-drafts` 工具被呼叫

---

### T-09 — 傳送知識更新指令

傳送：`我們的門市地址是台北市信義路五段7號`

**預期**：bot 確認已記錄，或回覆更新成功訊息  
**在 log 確認**：`parse-knowledge-update` 工具被呼叫

---

## 第四關：OAuth 連線流程

### T-10 — Google OAuth 連線頁面可存取

```bash
curl -sf -o /dev/null -w "%{http_code}" \
  "https://app.kachu.tw/auth/google/connect?tenant_id=test-tenant"
```

**預期**：302（重導向到 Google）或 200  
**失敗處理**：確認 `GOOGLE_OAUTH_CLIENT_ID` 與 `GOOGLE_REDIRECT_URI` 設定正確

---

### T-11 — OAuth state 不重複（Redis 驗證）

連續快速開啟兩次 `/auth/google/connect?tenant_id=test-tenant`，確認兩次的 Google 授權 URL 中 `state` 參數不同。

**預期**：每次 `state` 值唯一  
**說明**：這驗證 Redis-backed state store 運作正常，防止 CSRF

---

## 第五關：Dashboard 可存取

### T-12 — Dashboard 頁面

```bash
curl -sf -o /dev/null -w "%{http_code}" https://app.kachu.tw/dashboard
```

**預期**：未帶 `Authorization: Bearer <ADMIN_SERVICE_TOKEN>` 時為 401；帶正確 Bearer token 時為 200 OK  
**失敗處理**：查看 `dashboard_router` 相關 log

---

## 第六關：Webhook 安全驗證

### T-13 — 偽造 LINE webhook 應被拒絕

```bash
curl -sf -X POST https://app.kachu.tw/webhooks/line \
  -H "Content-Type: application/json" \
  -H "X-Line-Signature: invalidsignature" \
  -d '{"events":[]}'
```

**預期**：400 或 401（非 200）  
**說明**：確認 LINE signature 驗證有效，偽造請求無法通過

---

### T-14 — 無 signature 的 Google webhook 應被拒絕

```bash
curl -sf -X POST https://app.kachu.tw/webhooks/google/review \
  -H "Content-Type: application/json" \
  -d '{"test":"data"}'
```

**預期**：401 或 403  
**說明**：確認 Google webhook 的 shared secret / OIDC 驗證有作用

---

## 驗收結果記錄

| 測試 ID | 說明 | 結果 | 備注 |
|---|---|---|---|
| T-00 | pytest 全部通過 | ⬜ | |
| T-01 | Health endpoint | ⬜ | |
| T-02 | DB migration 狀態 | ⬜ | |
| T-03 | AgentOS 健康 | ⬜ | |
| T-04 | LINE Webhook Verify | ⬜ | |
| T-05 | 加好友歡迎訊息 | ⬜ | |
| T-06 | 一般文字訊息 | ⬜ | |
| T-07 | 圖片上傳分析 | ⬜ | |
| T-08 | 內容生成指令 | ⬜ | |
| T-09 | 知識更新指令 | ⬜ | |
| T-10 | Google OAuth 頁面 | ⬜ | |
| T-11 | OAuth state 唯一性 | ⬜ | |
| T-12 | Dashboard 可存取 | ⬜ | |
| T-13 | 偽造 LINE webhook 拒絕 | ⬜ | |
| T-14 | 無驗證 Google webhook 拒絕 | ⬜ | |

**T-00（pytest）+ 所有 T-01 以後全 ✅ 才可邀請第一個企業用戶。**

---

## 問題回報格式

跑測試時若發現問題，記錄如下：

```
測試 ID：T-XX
症狀：（實際看到什麼）
預期：（應該看到什麼）
Log：（貼上 docker compose logs kachu 相關行）
```
