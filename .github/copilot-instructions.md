# Kachu+ 開發 AI 指引

## 這個工作區在做什麼

我們正在從零開發 **Kachu+**，一個 SMB（個人商家）的 AI 商業夥伴 SaaS，透過 LINE 作為主要指揮介面。

主要產品定義文件：`Super8/Kachu+_產品定義文件.md`

**先讀第 10 行的 §0.5 RD 開發導讀**，裡面有最短必讀路徑、模組依賴圖、24 個具體開發任務（含完成條件）、開工前 Checklist。

---

## 工作區資料夾結構

| 資料夾 | 用途 |
|---|---|
| `Super8/` | Kachu+ 產品文件（本工作區的主體） |
| `Kachu_v2/` | 主要移植來源（FastAPI + Python，305 tests passing） |
| `AgentOS_real/` | Execution Runtime（WorkflowService + approval lifecycle） |
| `cresclab/` | Identity/Schema 設計參考（NestJS + TypeScript） |

---

## 開發原則

- **Greenfield 重建，但系統性借力**：不是 fork Kachu_v2，而是在新 codebase 中移植已驗證的模組
- 查任何函式/類別名稱前，先對回原始碼確認（見 `§12 四層地基`），不要靠文件描述猜
- Schema 以 `§7.4 資料層核心 tables` 為準，任何 table 異動要先確認與這章一致
- AgentOS `WorkflowService` 是唯一的 execution runtime，不要在業務層自己實作 task state machine

---

## 當前開發狀態

V1 尚未開始實作。五個模組的開發順序：

```
模組一（Onboarding + LINE）→ 模組二/三（並行）→ 模組四 → 模組五
```

詳細任務清單見 `Super8/Kachu+_產品定義文件.md` 第 44 行（§0.5 模組內任務順序）。
