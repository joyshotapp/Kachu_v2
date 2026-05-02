from __future__ import annotations

import re


CONSULTATION_CONVERSATION_TYPE = "consultation"
COMMAND_CONVERSATION_TYPE = "command"

CONSULTATION_KNOWLEDGE_CATEGORIES = frozenset({
    "basic_info",
    "product",
    "core_value",
    "goal",
    "pain_point",
    "style",
    "contact",
    "offer",
    "restriction",
    "document",
})

_BRAND_SUFFIX_HINTS = (
    "堂",
    "館",
    "坊",
    "店",
    "所",
    "行",
    "室",
    "舖",
    "鋪",
    "軒",
    "苑",
)
_BRAND_STOPWORDS = frozenset({
    "這是一張",
    "這張圖片",
    "這是一個",
    "這是一款",
    "關於",
    "圖片分析",
    "產品宣傳圖片",
    "產品圖片",
    "常見QA",
    "草本飲品",
    "促銷活動",
    "宣傳海報",
})

_DOCUMENT_STRONG_SIGNAL_KEYWORDS = (
    "品牌資訊",
    "主打",
    "產品",
    "品項/內容",
    "成分",
    "價格",
    "菜單",
    "服務",
    "地址",
    "店名",
    "核心價值",
    "電話",
    "聯絡",
    "LINE",
    "IG",
    "優惠",
    "活動",
    "風格",
    "語氣",
    "禁語",
    "注意事項",
)

_NOISE_PREFIXES = (
    "那你覺得",
    "你覺得",
    "請問",
    "可以幫我",
    "幫我",
    "我要",
)

_CONTACT_LABEL_PATTERNS = (
    ("地址", r"(?:地址|店址|門市地址)[:：]\s*([^\n]+)"),
    ("電話", r"(?:電話|TEL|Tel|手機)[:：]?\s*([0-9+()（）\-\s]{6,})"),
    ("LINE", r"(?:LINE(?:官方)?|Line(?:官方)?|官方LINE)[:：]?\s*([A-Za-z0-9@._-]+)"),
    ("IG", r"(?:IG|Instagram)[:：]?\s*([A-Za-z0-9._]+)"),
    ("Facebook", r"(?:Facebook|FB|粉專)[:：]?\s*([^\s，,\n]+)"),
    ("官網", r"(?:官網|網站|預約網址)[:：]?\s*(https?://[^\s]+|[^\s，,\n]+\.[A-Za-z]{2,}[^\s]*)"),
)
_OFFER_KEYWORDS = ("優惠", "活動", "折扣", "買一送一", "體驗", "限時", "方案", "贈")
_STYLE_KEYWORDS = ("風格", "語氣", "口吻", "調性", "文案要", "內容要")
_RESTRICTION_KEYWORDS = ("禁語", "避免", "不要", "不可", "禁止", "注意事項", "請勿", "敏感")


def parse_basic_info_text(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    patterns = {
        "brand_name": r"店名[:：]\s*([^，,\n]+)",
        "industry": r"行業[:：]\s*([^，,\n]+)",
        "address": r"地址[:：]\s*([^\n]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            fields[key] = match.group(1).strip()
    return fields


def is_low_signal_document_text(text: str) -> bool:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return True
    has_strong_signal = any(keyword in cleaned for keyword in _DOCUMENT_STRONG_SIGNAL_KEYWORDS)
    if len(cleaned) <= 12:
        return True
    if not has_strong_signal and cleaned.endswith(("?", "？", "嗎", "呢")):
        return True
    if not has_strong_signal and any(cleaned.startswith(prefix) for prefix in _NOISE_PREFIXES):
        return True
    if not has_strong_signal and any(token in cleaned for token in ("怎麼", "如何", "為什麼")):
        return True
    if has_strong_signal:
        return False
    return False


def summarize_document_highlight(text: str, *, max_length: int = 120) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    first_line = cleaned.splitlines()[0].strip()
    compact = " ".join(first_line.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 1].rstrip() + "…"


def _append_unique_fact(results: list[str], seen: set[str], fact: str, *, max_items: int) -> bool:
    cleaned = " ".join(str(fact or "").split()).strip().strip("，,。；;")
    if not cleaned or cleaned in seen:
        return False
    seen.add(cleaned)
    results.append(cleaned)
    return len(results) >= max_items


def _document_lines(text: str) -> list[str]:
    return [
        " ".join(line.split()).strip("•- ")
        for line in str(text or "").splitlines()
        if line.strip()
    ]


def _is_contact_fallback_line(line: str) -> bool:
    cleaned = line.strip()
    if not cleaned or cleaned.startswith(("【圖片分析】", "關鍵詞：", "品項/內容：")):
        return False
    if re.match(r"^(地址|店址|門市地址|電話|TEL|Tel|手機|LINE|Line|官方LINE|IG|Instagram|Facebook|FB|粉專|官網|網站|預約網址)[:：]", cleaned):
        return True
    return "http" in cleaned or "@" in cleaned


def _is_offer_fallback_line(line: str) -> bool:
    cleaned = line.strip()
    if not cleaned or cleaned.startswith(("【圖片分析】", "關鍵詞：", "品項/內容：")):
        return False
    has_offer_keyword = any(token in cleaned for token in _OFFER_KEYWORDS)
    has_price_signal = bool(re.search(r"\d+\s*(元|折|包)", cleaned))
    return has_offer_keyword and (has_price_signal or len(cleaned) <= 40)


def is_valid_contact_fact(text: str) -> bool:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned or cleaned.startswith(("【圖片分析】", "關鍵詞：", "品項/內容：")):
        return False
    if re.match(r"^(地址|店址|門市地址|電話|TEL|Tel|手機|LINE|Line|官方LINE|IG|Instagram|Facebook|FB|粉專|官網|網站|預約網址)[:：]", cleaned):
        return True
    return "http" in cleaned or "@" in cleaned


def is_valid_offer_fact(text: str) -> bool:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned or cleaned.startswith(("【圖片分析】", "關鍵詞：", "品項/內容：")):
        return False
    has_offer_keyword = any(token in cleaned for token in _OFFER_KEYWORDS)
    has_price_signal = bool(re.search(r"\d+\s*(元|折|包)", cleaned))
    return has_offer_keyword or has_price_signal


def is_valid_restriction_fact(text: str) -> bool:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned or cleaned.startswith(("【圖片分析】", "關鍵詞：", "品項/內容：")):
        return False
    return any(token in cleaned for token in _RESTRICTION_KEYWORDS)


def extract_document_product_facts(text: str, *, max_items: int = 4) -> list[str]:
    content = str(text or "")
    if not content or is_low_signal_document_text(content):
        return []

    results: list[str] = []
    seen: set[str] = set()

    for name, description in re.findall(
        r"'name'\s*:\s*'([^']+)'(?:[^\n{}]*?'description'\s*:\s*'([^']+)')?",
        content,
    ):
        name = name.strip()
        description = description.strip()
        if not name or len(name) > 40:
            continue
        fact = f"{name}：{description}" if description else name
        if _append_unique_fact(results, seen, fact, max_items=max_items):
            return results

    menu_match = re.search(r"菜單顯示\s*([^\n。]+)", content)
    if menu_match:
        for item in re.split(r"[、,，/]\s*", menu_match.group(1)):
            cleaned = item.strip()
            if _append_unique_fact(results, seen, cleaned, max_items=max_items):
                return results

    main_claim = re.search(r"主打\s*([^\n。；;]+)", content)
    if main_claim:
        cleaned = main_claim.group(1).strip()
        _append_unique_fact(results, seen, cleaned, max_items=max_items)

    return results[:max_items]


def extract_document_contact_facts(text: str, *, max_items: int = 4) -> list[str]:
    content = str(text or "")
    if not content or is_low_signal_document_text(content):
        return []

    results: list[str] = []
    seen: set[str] = set()

    for label, pattern in _CONTACT_LABEL_PATTERNS:
        for match in re.findall(pattern, content):
            fact = f"{label}：{match.strip()}"
            if _append_unique_fact(results, seen, fact, max_items=max_items):
                return results

    for line in _document_lines(content):
        if _is_contact_fallback_line(line):
            if _append_unique_fact(results, seen, line, max_items=max_items):
                return results

    return results[:max_items]


def extract_document_style_facts(text: str, *, max_items: int = 3) -> list[str]:
    content = str(text or "")
    if not content or is_low_signal_document_text(content):
        return []

    results: list[str] = []
    seen: set[str] = set()

    for match in re.findall(r"(?:品牌)?(?:語氣|口吻|風格|調性|文案風格|內容風格)[:：]\s*([^\n]+)", content):
        if _append_unique_fact(results, seen, match, max_items=max_items):
            return results

    for line in _document_lines(content):
        if any(token in line for token in _STYLE_KEYWORDS) and not any(token in line for token in _RESTRICTION_KEYWORDS):
            if _append_unique_fact(results, seen, line, max_items=max_items):
                return results

    return results[:max_items]


def extract_document_offer_facts(text: str, *, max_items: int = 3) -> list[str]:
    content = str(text or "")
    if not content or is_low_signal_document_text(content):
        return []

    results: list[str] = []
    seen: set[str] = set()

    for name, price in re.findall(
        r"'name'\s*:\s*'([^']+)'(?:[^\n{}]*?'price'\s*:\s*'([^']+)')?",
        content,
    ):
        fact = f"{name.strip()}：{price.strip()}" if price else name.strip()
        if any(token in fact for token in _OFFER_KEYWORDS) and _append_unique_fact(results, seen, fact, max_items=max_items):
            return results

    for line in _document_lines(content):
        if _is_offer_fallback_line(line):
            if _append_unique_fact(results, seen, line, max_items=max_items):
                return results

    return results[:max_items]


def extract_document_restriction_facts(text: str, *, max_items: int = 3) -> list[str]:
    content = str(text or "")
    if not content or is_low_signal_document_text(content):
        return []

    results: list[str] = []
    seen: set[str] = set()

    for match in re.findall(r"(?:禁語|限制|注意事項)[:：]\s*([^\n]+)", content):
        fact = f"注意事項：{match.strip()}"
        if _append_unique_fact(results, seen, fact, max_items=max_items):
            return results

    for line in _document_lines(content):
        if any(token in line for token in _RESTRICTION_KEYWORDS):
            if _append_unique_fact(results, seen, line, max_items=max_items):
                return results

    return results[:max_items]


def extract_brand_name_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    patterns = (
        r"店名[:：]\s*([^，,\n]+)",
        r"品牌[:：]\s*([^，,\n]+)",
        r"這是一張([A-Za-z0-9\u4e00-\u9fff（）()]{2,24})[「『\"]",
        r"這張圖片是([A-Za-z0-9\u4e00-\u9fff（）()]{2,24})[「『\"]",
        r"([A-Za-z0-9\u4e00-\u9fff（）()]{2,24})[「『\"]",
    )
    for pattern in patterns:
        for raw in re.findall(pattern, text):
            cleaned = _clean_brand_candidate(raw)
            if looks_like_brand_name(cleaned) and cleaned not in candidates:
                candidates.append(cleaned)
    return candidates


def looks_like_brand_name(candidate: str) -> bool:
    if not candidate:
        return False
    text = candidate.strip()
    if len(text) < 2 or len(text) > 20:
        return False
    if any(stop in text for stop in _BRAND_STOPWORDS):
        return False
    if any(text.startswith(prefix) for prefix in _BRAND_STOPWORDS):
        return False
    return any(hint in text for hint in _BRAND_SUFFIX_HINTS) or "（原" in text or "(" in text


def _clean_brand_candidate(candidate: str) -> str:
    text = candidate.replace("【圖片分析】", "").strip().strip("：:，,。.!！?？「」『』[]（）()")
    prefixes = (
        "這是一張",
        "這張圖片是",
        "這張圖片呈現的是",
        "這是一個",
        "這是一款",
        "關於",
        "品牌",
        "店名",
    )
    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):].strip().strip("：:，,。.!！?？「」『』[]（）()")
    text = text.replace("的產品宣傳圖片", "").replace("產品宣傳圖片", "")
    return text.strip()