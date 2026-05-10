from __future__ import annotations

import logging
import re
from typing import Any

from kachu_plus.models import BossRouteDecision, BossRouteMode
from kachu_plus.website_knowledge import contains_url

logger = logging.getLogger(__name__)

# ── EXECUTE keywords ──────────────────────────────────────────────────────────
# 商家明確要求執行某個動作

_EXECUTE_KW = frozenset([
    # 發文類
    "幫我寫", "幫我發", "寫一篇", "發一篇", "發一個", "寫個",
    "發文", "發布", "商家動態", "活動公告", "限時優惠",
    "寫文案", "宣傳貼文", "宣傳文案", "po文",
    "幫我規劃", "內容企劃", "貼文企劃", "發文企劃", "先給企劃",
    # 評論類
    "幫我回覆", "幫我處理", "回覆評論", "回那個", "回那則", "處理評論",
    "回那條", "回評論", "回負評", "回差評", "回好評",
    # 流量/報表類
    "幫我查", "幫我拉", "查流量", "看流量", "拉報告",
    "月報", "週報", "流量報告", "業績報告", "ga4",
    "FB成效", "FB 成效", "臉書成效", "臉書 成效", "Facebook成效", "Facebook 成效",
    "IG成效", "IG 成效", "Instagram成效", "Instagram 成效", "社群成效", "社群 成效", "Meta成效", "Meta 成效",
    "連接FB", "連接 FB", "連接IG", "連接 IG", "連接Meta", "連接 Meta", "連接Facebook", "連接 Facebook",
    "綁定FB", "綁定 FB", "綁定IG", "綁定 IG", "綁定Meta", "綁定 Meta",
    "重新授權FB", "重新授權 FB", "重新授權IG", "重新授權 IG", "重新授權Meta", "重新授權 Meta",
    "解除Meta", "解除 Meta", "解除FB", "解除 FB", "解除綁定Meta", "解除綁定 Meta",
    "目前連的是", "現在連的是", "哪個粉專", "Meta 狀態", "Meta狀態", "查看Meta", "查看 Meta",
    # 顧客記憶類（Kachu+ 新增）
    "哪些客人", "沉睡顧客", "沉睡客人", "查沉睡",
    "多久沒來", "流失顧客", "流失客人", "客人列表",
    # 店家資訊更新類
    "幫我更新", "更新營業", "更新地址", "更新電話",
    "更新菜單", "更新Google", "更新店家",
    # 臨時休業類（業務規則更新）
    "公休", "店休", "今天休", "今日休", "不營業",
    "打烊", "暫停營業", "休息日",
    # 標籤管理類（Kachu+ A-4）
    "建立標籤", "新增標籤", "新建標籤", "刪除標籤", "移除標籤", "停用標籤",
    "查看標籤", "我的標籤", "標籤列表", "顯示標籤", "有什麼標籤",
])

# ── CONSULT keywords ──────────────────────────────────────────────────────────
# 商家在問策略/建議/意見，不是要求立即執行

_CONSULT_KW = frozenset([
    # 詢問意見
    "你覺得", "你建議", "你認為", "你看",
    # 策略/方向問題
    "策略", "定位", "方向", "看法",
    "先討論", "討論一下", "想聊聊", "想聊", "想了解",
    # 原因/分析類
    "為什麼", "原因是",
    # 諮詢動詞（覆蓋「幫我分析」這類 CONSULT）
    "幫我分析", "幫我評估", "幫我想想",
    # How-to 諮詢（要怎麼/如何 + 非具體指令）
    "怎麼提升", "怎麼讓", "怎麼辦", "怎麼做才", "怎麼看",
    "如何提升", "如何讓", "如何才", "如何面對",
    "要怎麼讓", "要怎麼做",
    "應該怎麼", "應該如何", "應該先",
    # 確認/評估類
    "值得嗎", "合理嗎", "好嗎", "適合嗎",
    # 最佳實踐
    "最佳實踐", "有什麼建議",
])

_REVIEW_PATTERN = re.compile(r"評論|評價|留言")
_ACTION_PATTERN = re.compile(r"回|回覆|處理|看|查看|檢查")
_TAG_PATTERN = re.compile(r"標籤")


def classify_boss_message(text: str) -> BossRouteDecision:
    """
    Keyword-based BossRouteMode 分類（v1 同步版，零延遲）。

    分類規則：
    1. 先檢查 CONSULT 信號（advisory/strategy 語意）
    2. 再檢查 EXECUTE 信號（action command 語意）
    3. 兩者皆有 → CONSULT 優先（商家在問「要不要做」而非「做」）
    4. 都沒有 → CLARIFY

    參考：Kachu_v2 intent_router.py classify_text 的 keyword fast-path 設計。
    """
    has_consult = any(kw in text for kw in _CONSULT_KW)
    has_execute = any(kw in text for kw in _EXECUTE_KW)

    if not has_execute and _looks_like_execute(text):
        has_execute = True

    if has_execute and not has_consult:
        intent_label = _derive_intent_label(text)
        return BossRouteDecision(mode=BossRouteMode.EXECUTE, intent_label=intent_label)

    if has_consult:
        return BossRouteDecision(
            mode=BossRouteMode.CONSULT,
            consult_reply="我聽到了。讓我幫你想想——你可以把更多背景告訴我，或直接問你想知道的問題。",
        )

    return BossRouteDecision(
        mode=BossRouteMode.CLARIFY,
        clarify_question="你是想要我直接幫你做什麼，還是想先討論方向？",
    )


def _looks_like_execute(text: str) -> bool:
    if _looks_like_draft_status(text):
        return True
    if _looks_like_website_ingest(text):
        return True
    if _REVIEW_PATTERN.search(text) and _ACTION_PATTERN.search(text):
        return True
    if _TAG_PATTERN.search(text) and any(kw in text for kw in ("建", "建立", "新增", "新建", "刪", "移除", "停用", "看", "查看", "列出")):
        return True
    if "客人" in text and any(kw in text for kw in ("沒來", "多久", "超過", "流失", "沉睡")):
        return True
    return False


def _looks_like_draft_status(text: str) -> bool:
    if "草稿" not in text and "貼文" not in text and "回覆" not in text:
        return False
    status_signals = ("好了嗎", "好了沒", "完成了嗎", "完成沒", "進度", "狀態", "在哪", "怎麼還沒", "你不是要")
    return any(signal in text for signal in status_signals)


def _looks_like_website_ingest(text: str) -> bool:
    if not contains_url(text):
        return False
    lowered = text.lower()
    return (
        text.strip().startswith(("http://", "https://"))
        or any(keyword in text for keyword in ("官網", "網站", "首頁", "品牌資料", "品牌介紹", "給你", "參考這個"))
        or "website" in lowered
    )


def _derive_intent_label(text: str) -> str:
    """根據 EXECUTE 類型給出一個簡短的 intent 描述（給 log / downstream 用）。"""
    if _looks_like_draft_status(text):
        return "draft_status"
    if _looks_like_website_ingest(text):
        return "website_ingest"
    if any(kw in text for kw in ("內容企劃", "貼文企劃", "發文企劃", "先給企劃")):
        return "content_plan"
    if "規劃" in text and any(kw in text for kw in ("貼文", "發文", "商家動態", "內容", "文案")):
        return "content_plan"
    if any(kw in text for kw in ("重新授權FB", "重新授權 FB", "重新授權IG", "重新授權 IG", "重新授權Meta", "重新授權 Meta")):
        return "meta_reauth"
    if any(kw in text for kw in ("解除Meta", "解除 Meta", "解除FB", "解除 FB", "解除綁定Meta", "解除綁定 Meta")):
        return "meta_disconnect"
    if any(kw in text for kw in ("目前連的是", "現在連的是", "哪個粉專", "Meta 狀態", "Meta狀態", "查看Meta", "查看 Meta")):
        return "meta_status"
    if any(kw in text for kw in ("連接FB", "連接 FB", "連接IG", "連接 IG", "連接Meta", "連接 Meta", "連接Facebook", "連接 Facebook", "綁定FB", "綁定 FB", "綁定IG", "綁定 IG", "綁定Meta", "綁定 Meta")):
        return "meta_connect"
    if any(kw in text for kw in ("哪些客人", "沉睡顧客", "沉睡客人", "查沉睡", "流失顧客", "流失客人", "客人列表")):
        return "sleep_customer_query"
    if "客人" in text and any(kw in text for kw in ("沒來", "多久", "超過", "流失", "沉睡")):
        return "sleep_customer_query"
    if any(kw in text for kw in (
        "FB成效", "FB 成效", "臉書成效", "臉書 成效", "Facebook成效", "Facebook 成效",
        "IG成效", "IG 成效", "Instagram成效", "Instagram 成效", "社群成效", "社群 成效", "Meta成效", "Meta 成效",
    )):
        return "meta_insights"
    if any(kw in text for kw in ("回覆評論", "回那個", "回那則", "處理評論", "回負評", "回差評", "回好評", "回評論")):
        return "review_reply"
    if _REVIEW_PATTERN.search(text):
        return "review_reply"
    if any(kw in text for kw in ("發文", "寫一篇", "幫我寫", "幫我發", "商家動態", "活動公告", "寫文案")):
        return "google_post"
    if any(kw in text for kw in ("月報", "週報", "查流量", "看流量", "拉報告", "流量報告", "ga4")):
        return "analytics_report"
    if any(kw in text for kw in ("公休", "店休", "今天休", "今日休", "不營業", "打烊", "暫停營業")):
        return "business_profile_update"
    if any(kw in text for kw in ("幫我更新", "更新營業", "更新地址", "更新電話", "更新菜單", "更新Google", "更新店家")):
        return "knowledge_update"
    if any(kw in text for kw in (
        "建立標籤", "新增標籤", "新建標籤", "刪除標籤", "移除標籤", "停用標籤",
        "查看標籤", "我的標籤", "標籤列表", "顯示標籤", "有什麼標籤",
    )):
        return "tag_management"
    if _TAG_PATTERN.search(text):
        return "tag_management"
    return "general_execute"
