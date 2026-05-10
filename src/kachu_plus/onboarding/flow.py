from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kachu_plus.persistence.repository import KachuPlusRepository
    from kachu_plus.website_knowledge import WebsiteKnowledgeIngestionService

from kachu_plus.website_knowledge import contains_url, format_website_ingestion_reply

logger = logging.getLogger(__name__)

# ── Bot message templates（移植自 Kachu_v2 onboarding/flow.py，保留語意） ─────

_BOT_MESSAGES: dict[str, str] = {
    "welcome": (
        "👋 歡迎使用 Kachu+！\n"
        "我是你的 AI 商業夥伴，幫你管理品牌陣地、維繫顧客關係。\n\n"
        "在開始之前，我需要了解你的生意。只要幾分鐘 🙏\n\n"
        "請問你的店名是什麼？"
    ),
    "asking_industry": "謝謝！{name} 已記下 ✅\n\n你的行業類型是什麼？\n（例如：餐廳、咖啡廳、美甲店、網拍）",
    # Kachu+ 新增：模組三 sleep 計算用
    "asking_sleep_threshold": (
        "好的！\n\n"
        "你的客人通常幾天來一次算正常？\n"
        "（例如：每週、每月、每兩週，或直接說天數，例如 30）"
    ),
    "asking_address": (
        "了解！\n\n"
        "請告訴我你的地址或營業地點？\n"
        "（純網路銷售可以輸入「網路」）"
    ),
    "awaiting_docs": (
        "基本資料已儲存 ✅\n\n"
        "有任何品牌資料嗎？例如菜單、宣傳文字、產品介紹。\n"
        "直接傳給我，我會記下來；沒有的話傳「跳過」直接進入下一步 👇"
    ),
    "doc_received": "收到！繼續傳，或傳「完成」進入下一步 📄",
    "interview_q1": (
        "好！現在有三個簡單的問題，幫我更了解你 😊\n\n"
        "第 1 題：\n你跟別家最不一樣的地方是什麼？"
    ),
    "interview_q2": "很棒！\n\n第 2 題：\n你現在最大的困擾是什麼？",
    "interview_q3": "了解 🙏\n\n第 3 題：\n今年你最想做的一件事是什麼？",
    "completed": (
        "🎉 太好了！我已經更了解你的生意了。\n\n"
        "接下來你可以直接跟我說想做什麼，例如：\n"
        "• 「幫我寫一篇貼文」\n"
        "• 「哪些客人超過 60 天沒來」\n"
        "• 「幫我回覆評論」\n\n"
        "或者問我任何關於生意的問題 💬"
    ),
}

_SKIP_KEYWORDS = frozenset({"完成", "好了", "done", "跳過", "skip", "next"})

# 重新回答前一題的關鍵字（保守設計，避免誤觸發）
_REDO_KEYWORDS = ("重新回答", "重來", "上一題", "回到上一")

# 每個 interview step 儲存的 knowledge category
_STEP_CATEGORY: dict[str, str] = {
    "interview_q1": "core_value",
    "interview_q2": "pain_point",
}

# 往前退一步的 mapping
_PREV_STEP: dict[str, str] = {
    "interview_q2": "interview_q1",
    "interview_q3": "interview_q2",
}

# sleep_threshold 常見語言 → 天數（Kachu+ 新增）
_SLEEP_THRESHOLD_MAP = {
    "每天": 1,
    "每日": 1,
    "每週": 7,
    "每周": 7,
    "一週": 7,
    "一周": 7,
    "每兩週": 14,
    "每两週": 14,
    "兩週": 14,
    "两週": 14,
    "半個月": 15,
    "每月": 30,
    "一個月": 30,
    "一个月": 30,
    "每兩個月": 60,
    "每两個月": 60,
    "兩個月": 60,
    "两個月": 60,
}


def _detect_redo_step(content: str, current_step: str) -> str | None:
    """如果 content 是重新回答請求，回傳要退到的 step；否則回 None。"""
    c = content.strip()
    if "第一題" in c:
        return "interview_q1"
    if "第二題" in c and current_step == "interview_q3":
        return "interview_q2"
    if any(kw in c for kw in _REDO_KEYWORDS):
        return _PREV_STEP.get(current_step)
    return None


def _parse_sleep_threshold(text: str) -> int:
    """
    解析商家輸入的「客人回來頻率」，轉換為天數。
    無法解析時預設 30 天。
    """
    text = text.strip()
    for keyword, days in _SLEEP_THRESHOLD_MAP.items():
        if keyword in text:
            return days
    # 嘗試直接解析數字
    import re
    match = re.search(r"\d+", text)
    if match:
        n = int(match.group())
        if 1 <= n <= 365:
            return n
    return 30  # default


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _invalid_answer_prompt(step: str) -> dict[str, Any]:
    prompts = {
        "asking_name": "請直接用文字告訴我你的店名。",
        "asking_industry": "請直接用文字告訴我你的行業類型。",
        "asking_sleep_threshold": "請直接用文字回覆客人通常幾天來一次，像是「每週」或「30 天」。",
        "asking_address": "請直接用文字告訴我你的地址或營業地點。",
        "interview_q1": "請直接用文字回答第 1 題，讓我更了解你的特色。",
        "interview_q2": "請直接用文字回答第 2 題，告訴我你目前最大的困擾。",
        "interview_q3": "請直接用文字回答第 3 題，告訴我你今年最想完成的事。",
    }
    prompt = prompts.get(step, "請直接用文字回覆。")
    follow_up = _BOT_MESSAGES.get(step, "")
    if follow_up:
        return _text(f"{prompt}\n\n{follow_up}")
    return _text(prompt)


class OnboardingFlow:
    """
    LINE-based Day 0 onboarding 狀態機。

    States:
        new → asking_name → asking_industry → asking_sleep_threshold
            → asking_address → awaiting_docs
            → interview_q1 → interview_q2 → interview_q3 → completed

    設計繼承自 Kachu_v2 onboarding/flow.py。
    Kachu+ 新增：asking_sleep_threshold step（存 tenant.sleep_threshold，模組三沉睡計算用）。
    """

    def __init__(
        self,
        repo: "KachuPlusRepository",
        website_ingestion_service: "WebsiteKnowledgeIngestionService | None" = None,
    ) -> None:
        self._repo = repo
        self._website_ingestion_service = website_ingestion_service

    def is_in_onboarding(self, tenant_id: str) -> bool:
        return not self._repo.is_onboarding_complete(tenant_id)

    async def handle_message(
        self,
        tenant_id: str,
        msg_type: str,
        content: str,
        source_conversation_id: str = "",
    ) -> list[dict[str, Any]]:
        """
        處理商家訊息，回傳 LINE message objects（list of dict）。
        """
        state = self._repo.get_or_create_onboarding_state(tenant_id)
        step = state.step

        # 除了 new / awaiting_docs，其餘步驟都只接受非空文字，避免空值或圖片誤推進流程。
        if step not in {"new", "awaiting_docs"}:
            if msg_type != "text" or not content.strip():
                return [_invalid_answer_prompt(step)]

        handlers = {
            "new": self._handle_new,
            "asking_name": self._handle_asking_name,
            "asking_industry": self._handle_asking_industry,
            "asking_sleep_threshold": self._handle_asking_sleep_threshold,
            "asking_address": self._handle_asking_address,
            "awaiting_docs": self._handle_awaiting_docs,
            "interview_q1": self._handle_interview_q1,
            "interview_q2": self._handle_interview_q2,
            "interview_q3": self._handle_interview_q3,
        }

        handler = handlers.get(step)
        if handler is None:
            return []
        return await handler(tenant_id, content, source_conversation_id=source_conversation_id)

    # ── Step handlers ─────────────────────────────────────────────────────────

    async def _handle_new(self, tenant_id: str, _content: str, *, source_conversation_id: str = "") -> list[dict[str, Any]]:
        self._repo.update_onboarding_step(tenant_id, "asking_name")
        return [_text(_BOT_MESSAGES["welcome"])]

    async def _handle_asking_name(
        self, tenant_id: str, content: str, *, source_conversation_id: str = ""
    ) -> list[dict[str, Any]]:
        name = content.strip()
        tenant = self._repo.get_tenant(tenant_id)
        if tenant is not None:
            tenant.name = name
            self._repo.save_tenant(tenant)
        self._repo.update_onboarding_step(tenant_id, "asking_industry")
        return [_text(_BOT_MESSAGES["asking_industry"].format(name=name))]

    async def _handle_asking_industry(
        self, tenant_id: str, content: str, *, source_conversation_id: str = ""
    ) -> list[dict[str, Any]]:
        tenant = self._repo.get_tenant(tenant_id)
        if tenant is not None:
            tenant.industry_type = content.strip()
            self._repo.save_tenant(tenant)
        self._repo.update_onboarding_step(tenant_id, "asking_sleep_threshold")
        return [_text(_BOT_MESSAGES["asking_sleep_threshold"])]

    async def _handle_asking_sleep_threshold(
        self, tenant_id: str, content: str, *, source_conversation_id: str = ""
    ) -> list[dict[str, Any]]:
        """Kachu+ 新增 step：設定 sleep_threshold，onboarding 後模組三排程計算用。"""
        days = _parse_sleep_threshold(content)
        tenant = self._repo.get_tenant(tenant_id)
        if tenant is not None:
            tenant.sleep_threshold = days
            self._repo.save_tenant(tenant)
        logger.info("tenant=%s sleep_threshold set to %d days", tenant_id, days)
        self._repo.update_onboarding_step(tenant_id, "asking_address")
        return [_text(_BOT_MESSAGES["asking_address"])]

    async def _handle_asking_address(
        self, tenant_id: str, content: str, *, source_conversation_id: str = ""
    ) -> list[dict[str, Any]]:
        tenant = self._repo.get_tenant(tenant_id)
        if tenant is not None:
            tenant.address = content.strip()
            self._repo.save_tenant(tenant)
        self._repo.update_onboarding_step(tenant_id, "awaiting_docs")
        return [_text(_BOT_MESSAGES["awaiting_docs"])]

    async def _handle_awaiting_docs(
        self, tenant_id: str, content: str, *, source_conversation_id: str = ""
    ) -> list[dict[str, Any]]:
        if content.strip().lower() in _SKIP_KEYWORDS:
            self._repo.update_onboarding_step(tenant_id, "interview_q1")
            return [_text(_BOT_MESSAGES["interview_q1"])]
        if content.strip():
            self._repo.save_knowledge_entry(
                tenant_id=tenant_id,
                category="brand_material",
                content=content.strip(),
                source_conversation_id=source_conversation_id,
            )
            if self._website_ingestion_service is not None and contains_url(content):
                try:
                    result = await self._website_ingestion_service.ingest_from_message(
                        tenant_id=tenant_id,
                        text=content,
                        source_conversation_id=source_conversation_id,
                    )
                except Exception:
                    logger.exception("tenant=%s website ingestion failed during onboarding", tenant_id)
                else:
                    if result is not None:
                        return [_text(format_website_ingestion_reply(result, onboarding=True))]
        return [_text(_BOT_MESSAGES["doc_received"])]

    async def _handle_interview_q1(
        self, tenant_id: str, content: str, *, source_conversation_id: str = ""
    ) -> list[dict[str, Any]]:
        self._repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category="core_value",
            content=content.strip(),
            source_conversation_id=source_conversation_id,
        )
        self._repo.update_onboarding_step(tenant_id, "interview_q2")
        return [_text(_BOT_MESSAGES["interview_q2"])]

    async def _handle_interview_q2(
        self, tenant_id: str, content: str, *, source_conversation_id: str = ""
    ) -> list[dict[str, Any]]:
        redo_step = _detect_redo_step(content, "interview_q2")
        if redo_step:
            self._repo.delete_knowledge_entries_by_category(
                tenant_id, _STEP_CATEGORY.get(redo_step, "")
            )
            self._repo.update_onboarding_step(tenant_id, redo_step)
            return [_text("沒問題！讓我們重新來 ✍️\n\n" + _BOT_MESSAGES[redo_step])]

        self._repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category="pain_point",
            content=content.strip(),
            source_conversation_id=source_conversation_id,
        )
        self._repo.update_onboarding_step(tenant_id, "interview_q3")
        return [_text(_BOT_MESSAGES["interview_q3"])]

    async def _handle_interview_q3(
        self, tenant_id: str, content: str, *, source_conversation_id: str = ""
    ) -> list[dict[str, Any]]:
        redo_step = _detect_redo_step(content, "interview_q3")
        if redo_step:
            # 退回 q1：刪 core_value + pain_point；退回 q2：只刪 pain_point
            categories_to_delete = (
                ["core_value", "pain_point"] if redo_step == "interview_q1" else ["pain_point"]
            )
            for cat in categories_to_delete:
                self._repo.delete_knowledge_entries_by_category(tenant_id, cat)
            self._repo.update_onboarding_step(tenant_id, redo_step)
            return [_text("沒問題！讓我們重新來 ✍️\n\n" + _BOT_MESSAGES[redo_step])]

        self._repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category="goal",
            content=content.strip(),
            source_conversation_id=source_conversation_id,
        )

        # 彙整基本資訊 → knowledge entry（方便後續 RAG 查詢）
        tenant = self._repo.get_tenant(tenant_id)
        if tenant is not None:
            basic_info = (
                f"店名：{tenant.name}，"
                f"行業：{tenant.industry_type}，"
                f"地址：{tenant.address}，"
                f"沉睡閾值：{tenant.sleep_threshold} 天"
            )
            self._repo.save_knowledge_entry(
                tenant_id=tenant_id,
                category="basic_info",
                content=basic_info,
            )

        self._repo.update_onboarding_step(tenant_id, "completed")
        logger.info("Onboarding completed for tenant=%s", tenant_id)
        return [_text(_BOT_MESSAGES["completed"])]
