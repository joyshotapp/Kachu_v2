# Kachu Phase 0 Readiness + Knowledge Internalization Design

> 日期：2026-05-02
> 目的：定義 Phase 0 的使用者體驗、知識收集、知識內化、以及後續生成/建議/對話如何真正變得「越用越懂這個品牌」。

## 一、這份設計要解決的核心問題

Phase 0 不能只是平台串接流程。

如果使用者完成 interview、上傳品牌文件、傳了很多背景資訊，但 Kachu 在後續生成文案、提出建議、回應對話時仍像第一次見到這間店，使用者會直接覺得系統沒有學會。

所以 Phase 0 必須同時完成兩件事：

1. 建立「可開始使用」的 readiness。
2. 建立「可持續理解品牌」的 knowledge baseline。

前者讓使用者能開始做事。
後者讓使用者覺得 Kachu 真的在變懂。

兩者缺一不可。

## 二、目前 codebase 已有什麼、還缺什麼

### 已有基礎

1. Onboarding interview 會把基本答案存成 knowledge entries。
2. awaiting_docs 階段已支援圖片、檔案、文字上傳。
3. 文件解析已有 image parser 與 file parser。
4. retrieve-context 已能讀 knowledge entries、shared context、brand brief、owner brief。
5. generate-drafts 已會吃 brand_brief、owner_brief、preference memory、episodic memory。

### 目前不夠的地方

1. onboarding 寫入知識時大多直接走 repository，而不是統一經過 memory manager。
2. 文件、訪談、後續對話雖然「有存」，但沒有保證會即時轉成穩定可用的摘要與 embedding。
3. 文件上傳後目前比較像被記錄，而不是明確回饋「我學到了什麼」。
4. brand_brief 目前主要從既有 knowledge entries 摘取，還不夠像一份持續演進的品牌檔案。
5. 對話中的新事實、新偏好、新限制，沒有明確的內化判定與更新機制。
6. 使用者目前感受不到「你上傳的資料已經被吸收，現在會怎麼影響我之後的草稿」。

## 三、Phase 0 的正確產品目標

Phase 0 的最終完成條件不應該只是：

1. 基本資料填完。
2. 平台已授權。
3. 三題問完。

而應該是：

1. Kachu 已知道這家店是誰。
2. Kachu 已知道目前哪些渠道可用。
3. Kachu 已吸收第一批品牌知識。
4. Kachu 已能做出第一個不太像陌生人的成果。

## 四、使用者體驗應改成兩條並行主線

### 主線 A：Readiness

回答的是：現在能不能開始用。

這條主線處理：

1. 品牌最小身份建立。
2. 平台串接。
3. 渠道 readiness summary。
4. 第一個成功任務。

### 主線 B：Knowledge Internalization

回答的是：Kachu 之後會不會越來越懂你。

這條主線處理：

1. interview。
2. 文件與圖片上傳。
3. 後續對話中的新資訊吸收。
4. 品牌摘要與老闆偏好持續更新。

這兩條主線在體驗上應該並行，而不是先全部做完 B 才能開始 A。

## 五、Phase 0 的新流程

### Step 1：最小身份建立

目標：讓 Kachu 知道品牌基本輪廓。

收集項目：

1. 品牌名稱。
2. 品牌類型。
3. 地區或地址。

使用者感受：快速、沒有負擔。

### Step 2：渠道設定

目標：讓使用者把「經營渠道」接進來，而不是各別理解平台技術。

體驗形式：

1. Facebook。
2. Instagram。
3. Google 商家。

呈現方式必須完全一致，只是狀態不同。

### Step 3：品牌資料匯入

這是 Phase 0 的必要主步驟，不是可有可無的附屬功能。

Kachu 要明確告訴使用者：

你可以現在把現有品牌資料丟給我，我會先讀一輪，後面幫你寫文案和建議時會更像你。

可接受資料：

1. 菜單。
2. 產品目錄。
3. 舊貼文。
4. DM、海報、活動圖。
5. 品牌介紹文件。
6. 常見問答。
7. 價格表。
8. 店內環境、招牌、商品照片。

### Step 4：品牌訪談

訪談仍保留，但角色要改變。

它不是上線門檻，而是第二層理解補強。

建議保留三題，但語意要更精準：

1. 你最希望客人記住你什麼？
2. 你現在最想改善的經營問題是什麼？
3. 接下來 3 個月你最想先做到哪件事？

### Step 5：Kachu 回報「我已經學到什麼」

這一步現在缺得最明顯。

使用者在上傳資料和回答問題後，系統不能只是回一句「收到」。

應該回一個品牌吸收摘要，例如：

1. 我知道你主打的是什麼。
2. 我知道你現在最在意的經營目標。
3. 我看到你目前常用的產品與服務描述方式。
4. 之後我會先用這些資訊幫你起草。

這個回饋是讓使用者感受到「資料真的進系統了」的關鍵。

### Step 6：開始第一個真任務

Readiness 與 baseline knowledge 建立後，直接引導第一個行動。

例如：

1. 傳一張照片，幫你做第一篇貼文。
2. 或先請我整理你品牌的介紹說法。

## 六、知識內化的正確系統模型

Kachu 不能把所有資料都視為同一層「知識庫條目」。

至少要拆成四層。

### Layer 1：Raw Inputs

原始來源，不做過度抽象。

包含：

1. interview 原文。
2. 文件解析全文。
3. 圖片解析摘要。
4. 老闆後續對話原文。

用途：保留原始脈絡與可追溯性。

### Layer 2：Structured Facts

從 raw inputs 抽取成穩定事實。

例如：

1. 品牌名稱。
2. 地址。
3. 主打產品。
4. 價格資訊。
5. 服務特色。
6. 品牌核心價值。
7. 當前經營目標。
8. 禁語或敏感限制。

這層是後續 RAG 和 brand brief 的主體。

### Layer 3：Working Briefs

這層是給生成與建議直接用的摘要。

至少應有：

1. brand_brief。
2. owner_brief。
3. channel_readiness_brief。
4. onboarding_absorption_summary。

特性：

1. 可重建。
2. 可覆寫。
3. 有 TTL，但不是一次性。

### Layer 4：Behavior Memory

這層是使用後才會慢慢長出來的「怎樣才像這個老闆」。

包含：

1. edited drafts。
2. rejected drafts。
3. 老闆常用詞。
4. 老闆常否定的語氣。
5. 決策偏好。

這層讓系統從「理解品牌」進化到「理解這位老闆」。

## 七、文件與對話該怎麼被真正內化

### 文件不是只存起來，要被吸收成兩份結果

每次文件匯入後，系統至少要產生兩種結果：

1. 原始解析內容。
2. 吸收後的品牌更新摘要。

例如使用者上傳菜單與舊貼文後，Kachu 應該能更新出：

1. 目前可識別的主打商品有哪些。
2. 常見價格帶。
3. 常出現的品牌語氣。
4. 適合的內容主軸。

### 對話不是只當聊天紀錄，要有內化判定

不是每句對話都進品牌知識。

需要判定哪些對話屬於：

1. 穩定事實。
2. 短期目標。
3. 老闆偏好。
4. 一次性情緒或閒聊。

只有前 3 類應內化。

### 系統需具備「吸收事件」概念

當使用者上傳文件、補充關鍵描述、修正品牌說法時，系統應觸發一個 absorption event。

這個 event 負責：

1. 寫入 raw knowledge。
2. 產 embedding。
3. 更新 structured facts。
4. 重建 brand_brief / owner_brief。
5. 對使用者回報吸收結果。

## 八、使用者體驗上一定要看得見的回饋

這是體驗好壞的關鍵。

使用者上傳資料後，系統要讓他看到三件事：

1. 我收到了什麼。
2. 我看懂了什麼。
3. 這會如何影響之後的內容與建議。

建議回饋格式：

1. 已讀取：菜單圖片 3 張、舊貼文 2 則。
2. 我目前理解你的主打是：肩頸保養、日常調理、天然保健。
3. 之後我會優先用這些主題幫你寫貼文與建議。

這樣使用者才會覺得 Kachu 真的在建立企業理解，不是單純收檔案。

## 九、Phase 0 完成時，Kachu 至少應掌握的內容

### 品牌層

1. 品牌名稱。
2. 產業類型。
3. 地區或地址。
4. 核心價值。
5. 代表性產品或服務。
6. 初步品牌語氣。

### 經營層

1. 近期最重要目標。
2. 當前痛點。
3. 本月可優先推動的主題。

### 渠道層

1. Facebook readiness。
2. Instagram readiness。
3. Google Business readiness。
4. 本次 first task 可發去哪裡。

### 老闆層

1. 溝通偏好。
2. 決策偏好。
3. 是否傾向簡短、直接、可執行建議。

## 十、後續生成時應如何使用這些知識

### 內容生成

每次 generate-drafts 應先吃：

1. brand_brief。
2. owner_brief。
3. relevant document facts。
4. preference memory。
5. episode memory。

### 顧問建議

Business consultant 不應只根據最近一句話，而應同時看：

1. 品牌主體。
2. 當前目標。
3. 上傳文件中的商業資訊。
4. 最近對話主題。

### 後續一般對話

當老闆問「你覺得我這週該推什麼」時，Kachu 的回答應反映：

1. 這家店主打什麼。
2. 現在想達成什麼目標。
3. 最近提供了哪些新品/活動資料。

不應該每次都像 generic 顧問。

## 十一、產品上必須承諾的體驗原則

1. 使用者只要提供過一次的重要資訊，Kachu 就應盡量在之後持續使用。
2. Kachu 若更新了品牌理解，應讓使用者感知到。
3. 使用者可補件，不需要重新 onboarding。
4. 品牌理解是持續演進，不是 Day 0 一次填完就結束。
5. 平台 readiness 和品牌理解是兩條並行主線，但對使用者感受上必須整合成同一個 onboarding 體驗。

## 十二、實作上的具體調整方向

### A. Onboarding Flow

1. 將 awaiting_docs 從「可選附件步驟」提升為正式的品牌資料匯入步驟。
2. 補一個「吸收摘要」回覆，而不是單純 doc_received。
3. 將 interview 與 docs 結束後，觸發一次 brand absorption refresh。

### B. Knowledge Write Path

1. onboarding 期間的知識寫入應統一走 memory manager，而不是直接 save_knowledge_entry。
2. 所有文件、訪談答案、重要文字補充，都要自動補 embedding。
3. 對話需增加內化判定，而不是全部只存在 conversations。

### C. Brief Layer

1. brand_brief 需要明確吸收 document category 的重點。
2. owner_brief 需要吃 onboarding 與 general conversation 的偏好訊號。
3. 新增 onboarding_absorption_summary 作為 Phase 0 的使用者可見成果。

### D. UX Layer

1. 在 LINE 中顯示 readiness summary。
2. 在 LINE 中顯示 knowledge absorption summary。
3. 給使用者一個明確訊息：我已學到什麼，現在可以開始做什麼。

## 十三、驗收標準

這部分如果沒做到，就不能說 Phase 0 體驗完整。

1. 使用者完成 Phase 0 後，Kachu 能正確說出品牌基本輪廓。
2. 使用者上傳的文件內容會影響後續 draft generation。
3. 使用者補充的新對話事實，之後詢問時能被引用。
4. Kachu 會主動回饋自己吸收了哪些品牌資訊。
5. 第一次草稿不再像完全不了解品牌的通用文案。

## 十四、結論

Phase 0 的本質不是「讓使用者完成設定」。

Phase 0 的本質是：

1. 讓 Kachu 準備好開始工作。
2. 讓使用者相信 Kachu 已開始理解這家店。

平台串接解決的是第一件事。
知識內化解決的是第二件事。

真正好的 Phase 0，必須同時完成兩者。