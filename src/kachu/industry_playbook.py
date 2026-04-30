from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class IndustryProfile:
    name: str
    tone: str
    customer_motivations: tuple[str, ...]
    market_watchpoints: tuple[str, ...]
    content_angles: tuple[str, ...]
    consultant_focus: tuple[str, ...]


_GENERIC_PROFILE = IndustryProfile(
    name="一般服務業",
    tone="專業、親切、可信任",
    customer_motivations=("降低決策風險", "想先確認品質", "期待被清楚引導"),
    market_watchpoints=("口碑是否穩定", "訊息是否一致", "是否有明確主打差異"),
    content_angles=("品牌信任", "顧客案例", "近期亮點"),
    consultant_focus=("把主打價值講清楚", "縮短顧客理解成本", "讓行動呼籲更明確"),
)

_INDUSTRY_PROFILES: dict[str, IndustryProfile] = {
    "restaurant": IndustryProfile(
        name="餐飲",
        tone="溫暖、有食慾、在地可信賴",
        customer_motivations=("想知道好不好吃", "在意價格與份量", "希望用餐決策快速"),
        market_watchpoints=("尖峰時段流量", "菜單更新頻率", "評論中的服務與口味關鍵詞"),
        content_angles=("新品與季節菜色", "熱賣餐點與客人回饋", "節慶套餐與限時活動"),
        consultant_focus=("把招牌品項說得具體", "強化食材與場景感", "把到店理由說明白"),
    ),
    "cafe": IndustryProfile(
        name="咖啡廳",
        tone="質感、放鬆、有品味",
        customer_motivations=("找氣氛", "找值得拍照的內容", "找穩定品質與停留體驗"),
        market_watchpoints=("商圈話題性", "空間與新品題材", "回訪誘因是否清楚"),
        content_angles=("空間氛圍", "季節飲品", "甜點與限定組合"),
        consultant_focus=("保持視覺一致性", "建立固定招牌題材", "把來店情境寫出來"),
    ),
    "beauty": IndustryProfile(
        name="美業",
        tone="專業、細緻、值得信任",
        customer_motivations=("在意效果與安全", "在意專業度", "需要降低第一次嘗試焦慮"),
        market_watchpoints=("前後對比說服力", "檔期與回購節奏", "評論中的專業與細心感"),
        content_angles=("術前術後說明", "案例分享", "檔期方案與保養觀念"),
        consultant_focus=("強調專業與流程透明", "降低顧客猶豫", "把效果與限制講清楚"),
    ),
    "retail": IndustryProfile(
        name="零售",
        tone="清楚、俐落、有購買誘因",
        customer_motivations=("想快速知道值不值得買", "在意價格與差異", "需要清楚的選購理由"),
        market_watchpoints=("新品節奏", "熱賣品類", "促銷是否過度依賴折扣"),
        content_angles=("熱銷商品", "使用情境", "檔期優惠與組合搭配"),
        consultant_focus=("提高商品比較清晰度", "增加購買情境", "控制促銷節奏"),
    ),
}

_INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "restaurant": ("餐廳", "小吃", "便當", "早餐", "火鍋", "餐飲", "麵店", "飲料店"),
    "cafe": ("咖啡", "咖啡廳", "甜點", "下午茶"),
    "beauty": ("美甲", "美睫", "美容", "沙龍", "髮廊", "護膚", "美業"),
    "retail": ("零售", "選物", "服飾", "電商", "網拍", "門市", "商品"),
}

_MONTHLY_MARKET_EVENTS: dict[int, tuple[dict[str, str], ...]] = {
    1: ({"name": "新年開工", "theme": "新年新目標", "focus": "檔期開跑、回訪誘因"},),
    2: ({"name": "情人節", "theme": "雙人／送禮題材", "focus": "限定組合與情境文案"},),
    3: ({"name": "春季換季", "theme": "新品與換季需求", "focus": "更新亮點與場景化內容"},),
    4: ({"name": "清明連假", "theme": "連假安排", "focus": "提前預約與旅遊人流"},),
    5: ({"name": "母親節", "theme": "感謝與送禮", "focus": "套餐、禮盒、體驗優惠"},),
    6: ({"name": "畢業季", "theme": "聚會與慶祝", "focus": "團體需求與紀念內容"},),
    7: ({"name": "暑假", "theme": "夏季與親子", "focus": "出遊、聚餐、夏季限定"},),
    8: ({"name": "父親節", "theme": "家庭聚餐與送禮", "focus": "家庭客與實用方案"},),
    9: ({"name": "中秋節", "theme": "送禮與聚會", "focus": "禮盒、活動、聚餐內容"},),
    10: ({"name": "雙十與秋季檔期", "theme": "秋季活動", "focus": "檔期主題與限時優惠"},),
    11: ({"name": "雙11", "theme": "促購", "focus": "限時優惠與轉換優化"},),
    12: ({"name": "聖誕跨年", "theme": "聚會與送禮", "focus": "節慶氛圍與預約檔期"},),
}


def normalize_industry(industry_type: str) -> str:
    text = (industry_type or "").strip().lower()
    if not text:
        return "generic"
    for normalized, keywords in _INDUSTRY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return normalized
    return "generic"


def get_industry_profile(industry_type: str) -> IndustryProfile:
    return _INDUSTRY_PROFILES.get(normalize_industry(industry_type), _GENERIC_PROFILE)


def get_market_calendar(industry_type: str, *, now: datetime | None = None) -> list[dict[str, str]]:
    current = now or datetime.now(timezone.utc)
    month_events = list(_MONTHLY_MARKET_EVENTS.get(current.month, ()))
    profile = get_industry_profile(industry_type)
    month_events.append(
        {
            "name": f"{profile.name}固定經營重點",
            "theme": "品牌一致性",
            "focus": "維持訊息一致、主打差異清楚、持續收集顧客回饋",
        }
    )
    return month_events


def build_industry_context(industry_type: str) -> dict[str, object]:
    profile = get_industry_profile(industry_type)
    return {
        "industry_name": profile.name,
        "recommended_tone": profile.tone,
        "customer_motivations": list(profile.customer_motivations),
        "market_watchpoints": list(profile.market_watchpoints),
        "content_angles": list(profile.content_angles),
        "consultant_focus": list(profile.consultant_focus),
        "market_calendar": get_market_calendar(industry_type),
    }