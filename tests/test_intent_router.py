"""
任務 1-3 驗收測試：Intent router 分類準確率 ≥ 90%（100 筆測資）。

分類規則：
- EXECUTE：商家要求執行特定動作（發文、回評論、查流量、查沉睡、更新資訊）
- CONSULT：商家詢問策略/建議/意見（你覺得、怎麼提升、為什麼、策略方向）
- CLARIFY：意圖不清楚，需追問（無 EXECUTE 也無 CONSULT 信號）
"""
from __future__ import annotations

import pytest

from kachu_plus.intent_router import classify_boss_message
from kachu_plus.models import BossRouteMode

E = BossRouteMode.EXECUTE
C = BossRouteMode.CONSULT
X = BossRouteMode.CLARIFY

# 100 筆測資：(輸入文字, 預期 mode)
TEST_CASES: list[tuple[str, BossRouteMode]] = [
    # ── EXECUTE（50 筆）──────────────────────────────────────────────────────
    # 發文類
    ("幫我寫一篇貼文", E),
    ("幫我發一篇Google動態", E),
    ("寫一篇宣傳週年慶的貼文", E),
    ("幫我發文介紹新服務", E),
    ("發一個活動公告", E),
    ("發布限時優惠訊息", E),
    ("寫個商家動態", E),
    ("幫我寫文案宣傳烤雞", E),
    ("幫我發今天的特餐推薦", E),
    ("發一篇關於美甲新款的貼文", E),
    # 評論類
    ("幫我回覆評論", E),
    ("幫我回那個一星評論", E),
    ("幫我處理這則負評", E),
    ("回那個說服務不好的評論", E),
    ("幫我回那則差評", E),
    ("回覆客人的好評", E),
    ("幫我處理評論", E),
    ("回那條評論", E),
    ("幫我回評論", E),
    ("回那個差評", E),
    # 流量/報表類
    ("幫我查一下流量", E),
    ("幫我拉一份月報", E),
    ("給我看本週流量報告", E),
    ("拉一份GA4報告", E),
    ("幫我看一下本月業績報告", E),
    ("查流量給我看", E),
    ("看流量數據", E),
    ("幫我拉週報", E),
    ("幫我查本月業績", E),
    ("流量報告給我", E),
    # 顧客記憶類（Kachu+ 新增）
    ("哪些客人超過60天沒來", E),
    ("沉睡顧客有哪些", E),
    ("查沉睡客人列表", E),
    ("沉睡客人列表給我看", E),
    ("哪些客人超過一個月沒消費", E),
    ("幫我查哪些客人快流失了", E),
    ("流失客人有哪些", E),
    ("查一下哪些客人沉睡了", E),
    ("客人列表，超過45天沒來的", E),
    ("哪些顧客多久沒來了", E),
    # 店家資訊更新類
    ("更新營業時間", E),
    ("幫我更新Google商家資訊", E),
    ("更新電話號碼", E),
    ("幫我更新地址", E),
    ("更新菜單資訊", E),
    # 臨時休業類
    ("今天公休", E),
    ("今日店休", E),
    ("明天不營業，幫我更新Google", E),
    ("今天提早打烊", E),
    ("今天休息", E),

    # ── CONSULT（35 筆）──────────────────────────────────────────────────────
    ("你覺得我要怎麼提升回購率？", C),
    ("你覺得我的貼文風格好嗎？", C),
    ("你建議我先做什麼？", C),
    ("你認為我應該先專注在哪個平台？", C),
    ("我的生意越來越難做，你覺得怎麼辦？", C),
    ("為什麼我的Google評分一直不高？", C),
    ("為什麼客人不回來？", C),
    ("為什麼要管評論？", C),
    ("怎麼提升顧客回購率？", C),
    ("怎麼讓客人回來？", C),
    ("怎麼讓新客變熟客？", C),
    ("怎麼做才能讓客人更滿意？", C),
    ("怎麼看待競爭對手的策略？", C),
    ("如何讓更多人找到我的店？", C),
    ("如何提升Google評分？", C),
    ("如何面對負評？", C),
    ("如何才能讓排名更高？", C),
    ("先討論一下行銷策略", C),
    ("討論一下我的品牌定位", C),
    ("想聊聊我的定位方向", C),
    ("想了解一下顧客為什麼會沉睡", C),
    ("幫我分析為什麼客人流失", C),
    ("幫我評估現在的行銷效果", C),
    ("幫我想想怎麼提升生意", C),
    ("要怎麼讓客人增加消費頻率？", C),
    ("要怎麼做才能讓生意更好？", C),
    ("應該怎麼設定忠誠度計畫？", C),
    ("應該如何經營社群媒體？", C),
    ("應該先從哪個方向切入？", C),
    ("做LINE行銷值得嗎？", C),
    ("這個服務定價合理嗎？", C),
    ("發文頻率這樣好嗎？", C),
    ("有什麼建議可以讓評分提升？", C),
    ("最佳實踐是什麼？", C),
    ("我的品牌策略方向對嗎？", C),

    # ── CLARIFY（15 筆）──────────────────────────────────────────────────────
    # 無 EXECUTE 也無 CONSULT 關鍵字，意圖不明
    ("有個評論", X),
    ("最近客人有點少", X),
    ("想做點什麼", X),
    ("幫忙", X),
    ("有問題", X),
    ("客人回饋", X),
    ("最近在思考", X),
    ("考慮一件事", X),
    ("你有空嗎", X),
    ("最近生意", X),
    ("有新東西", X),
    ("想問你", X),
    ("生意的事", X),
    ("需要幫忙", X),
    ("有件事", X),
]

assert len(TEST_CASES) == 100, f"Expected 100 test cases, got {len(TEST_CASES)}"


def test_intent_router_accuracy() -> None:
    """整體準確率 ≥ 90%（100 筆中至少 90 筆正確）。"""
    correct = 0
    errors: list[str] = []
    for text, expected in TEST_CASES:
        decision = classify_boss_message(text)
        if decision.mode == expected:
            correct += 1
        else:
            errors.append(f"  [{expected.value}→{decision.mode.value}] {text!r}")

    accuracy = correct / len(TEST_CASES)
    error_report = "\n".join(errors) if errors else "(none)"
    assert accuracy >= 0.90, (
        f"Accuracy {correct}/{len(TEST_CASES)} = {accuracy:.1%} < 90%\n"
        f"Wrong predictions:\n{error_report}"
    )


@pytest.mark.parametrize("text,expected", [
    # EXECUTE — 包含完整 EXECUTE 關鍵字，無 CONSULT 關鍵字
    ("幫我寫一篇貼文", E),
    ("哪些客人超過60天沒來", E),
    ("今天公休", E),
    # CONSULT — 明確詢問意見
    ("你覺得我要怎麼提升回購率？", C),
    ("為什麼客人不回來？", C),
    ("幫我分析為什麼客人流失", C),
    # CLARIFY — 無明確信號
    ("有個評論", X),
    ("最近生意", X),
])
def test_individual_cases(text: str, expected: BossRouteMode) -> None:
    assert classify_boss_message(text).mode == expected


def test_execute_returns_intent_label() -> None:
    d = classify_boss_message("哪些客人超過60天沒來")
    assert d.mode == BossRouteMode.EXECUTE
    assert d.intent_label == "sleep_customer_query"


def test_meta_insights_query_returns_meta_intent_label() -> None:
    d = classify_boss_message("幫我看 Facebook 成效")
    assert d.mode == BossRouteMode.EXECUTE
    assert d.intent_label == "meta_insights"


def test_draft_status_follow_up_returns_execute_intent() -> None:
    d = classify_boss_message("草稿好了嗎")
    assert d.mode == BossRouteMode.EXECUTE
    assert d.intent_label == "draft_status"


def test_content_plan_request_returns_content_plan_intent() -> None:
    d = classify_boss_message("幫我規劃一個母親節貼文企劃")
    assert d.mode == BossRouteMode.EXECUTE
    assert d.intent_label == "content_plan"


def test_website_url_message_returns_website_ingest_intent() -> None:
    d = classify_boss_message("給你官網可以嗎？ https://seasonwell.com.tw/")
    assert d.mode == BossRouteMode.EXECUTE
    assert d.intent_label == "website_ingest"


@pytest.mark.parametrize(
    ("text", "intent_label"),
    [
        ("我要連接 FB/IG", "meta_connect"),
        ("我要串接meta帳號", "meta_connect"),
        ("我要重新授權 FB/IG", "meta_reauth"),
        ("我現在連的是哪個粉專", "meta_status"),
        ("我要解除 Meta 連接", "meta_disconnect"),
    ],
)
def test_meta_connection_commands_return_expected_intent_labels(text: str, intent_label: str) -> None:
    d = classify_boss_message(text)
    assert d.mode == BossRouteMode.EXECUTE
    assert d.intent_label == intent_label


@pytest.mark.parametrize(
    ("text", "intent_label"),
    [
        ("看一下最近有沒有評論要回", "review_reply"),
        ("幫我建一個 VIP 標籤", "tag_management"),
        ("有沒有客人超過 45 天沒來", "sleep_customer_query"),
    ],
)
def test_natural_language_execute_cases(text: str, intent_label: str) -> None:
    d = classify_boss_message(text)
    assert d.mode == BossRouteMode.EXECUTE
    assert d.intent_label == intent_label


def test_consult_returns_reply() -> None:
    d = classify_boss_message("你覺得我要怎麼提升回購率？")
    assert d.mode == BossRouteMode.CONSULT
    assert len(d.consult_reply) > 0


def test_capability_question_returns_consult_capability_reply() -> None:
    d = classify_boss_message("你能幫我做什麼？")
    assert d.mode == BossRouteMode.CONSULT
    assert "Google 商家動態" in d.consult_reply


def test_greeting_returns_consult_greeting_reply() -> None:
    d = classify_boss_message("你好")
    assert d.mode == BossRouteMode.CONSULT
    assert d.consult_reply.startswith("你好，我在")


def test_content_angle_question_returns_consult() -> None:
    d = classify_boss_message("我想做母親節，但不知道怎麼切角度")
    assert d.mode == BossRouteMode.CONSULT
    assert d.intent_label == "content_consult"


def test_clarify_returns_question() -> None:
    d = classify_boss_message("最近生意")
    assert d.mode == BossRouteMode.CLARIFY
    assert len(d.clarify_question) > 0
