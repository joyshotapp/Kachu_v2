from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..knowledge_capture import KnowledgeCaptureService
from ..persistence import KachuRepository

if TYPE_CHECKING:
    from ..context_brief_manager import ContextBriefManager
    from ..memory import MemoryManager
    from ..config import Settings
    from ..intent_router import IntentRouter

logger = logging.getLogger(__name__)

# ── Bot message templates ─────────────────────────────────────────────────────

_BOT_MESSAGES: dict[str, str] = {
    "welcome": (
        "👋 歡迎使用 Kachu！\n"
        "我是你的 AI 數位幕僚，幫你管理社群、回覆評論、解答顧客問題。\n\n"
        "在開始之前，我需要了解你的生意。只要幾分鐘 🙏\n\n"
        "請問你的店名是什麼？"
    ),
    "asking_industry": "謝謝！{name} 已記下 ✅\n\n你的行業類型是什麼？\n（例如：餐廳、咖啡廳、美甲店、網拍）",
    "asking_address": (
        "好的！\n\n"
        "請告訴我你的地址或營業地點？\n"
        "（純網路銷售可以輸入「網路」）"
    ),
    "awaiting_docs": (
        "基本資料已儲存 ✅\n\n"
        "現在可以傳給我任何你已有的資料：\n"
        "• 菜單 / 產品目錄\n"
        "• 舊的宣傳文字或截圖\n"
        "• 任何圖片或文件\n\n"
        "我會幫你消化，讓我更了解你的生意。\n"
        "完成後傳「完成」，或傳「跳過」直接進入下一步 👇"
    ),
    "doc_received": "收到！繼續傳，或傳「完成」進行下一步 📄",
    "interview_q1": (
        "好！現在有三個簡單的問題，幫我更了解你 😊\n\n"
        "第 1 題：\n你跟別家最不一樣的地方是什麼？"
    ),
    "interview_q2": "很棒！\n\n第 2 題：\n你現在最大的困擾是什麼？",
    "interview_q3": "了解 🙏\n\n第 3 題：\n今年你最想做的一件事是什麼？",
    "completed": (
        "🎉 太好了！我已經更了解你的生意了。\n\n"
        "接下來我會根據目前已就緒的渠道，陪你開始第一個任務。\n\n"
        "如果平台已接好，直接傳一張你想發布的照片給我 📸"
    ),
}

_SKIP_KEYWORDS = {"完成", "好了", "done", "跳過", "skip", "next"}

# Keywords that indicate the user wants to re-answer a previous question.
# Intentionally conservative to avoid false positives on real answers.
_REDO_KEYWORDS = ("重新回答", "重來", "上一題", "回到上一")

# Maps each interview step to its saved knowledge category, used for cleanup on redo.
_STEP_CATEGORY: dict[str, str] = {
    "interview_q1": "core_value",
    "interview_q2": "pain_point",
}

# Maps each step to the previous step for generic "go back" redo.
_PREV_STEP: dict[str, str] = {
    "interview_q2": "interview_q1",
    "interview_q3": "interview_q2",
}


def _detect_redo_step(content: str, current_step: str) -> str | None:
    """Return the step to roll back to if *content* is a redo request, else None."""
    c = content.strip()
    # Explicit question references take priority
    if "第一題" in c:
        return "interview_q1"
    if "第二題" in c and current_step == "interview_q3":
        return "interview_q2"
    # Generic redo keywords → go back one step
    if any(kw in c for kw in _REDO_KEYWORDS):
        return _PREV_STEP.get(current_step)
    return None


_ONBOARDING_AHA_TOPIC_TEMPLATES = (
    "認識{brand}：第一次來店前最值得知道的亮點",
    "為什麼大家會選擇{brand}：主打特色與推薦理由",
    "這週想讓更多人知道的{industry}亮點：來店前先看這篇",
)


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


class OnboardingFlow:
    """
    LINE-based DAY 0 onboarding state machine.

    States (stored in OnboardingStateTable.step):
        new → asking_name → asking_industry → asking_address
            → awaiting_docs → interview_q1 → interview_q2
            → interview_q3 → completed
    """

    def __init__(
        self,
        repo: KachuRepository,
        settings: "Settings | None" = None,
        intent_router: "IntentRouter | None" = None,
        memory_manager: "MemoryManager | None" = None,
        context_brief_manager: "ContextBriefManager | None" = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._intent_router = intent_router
        self._memory = memory_manager
        self._context_brief_manager = context_brief_manager
        self._knowledge_capture = KnowledgeCaptureService(
            repo,
            settings,
            memory_manager=memory_manager,
            context_brief_manager=context_brief_manager,
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def is_in_onboarding(self, tenant_id: str) -> bool:
        """Return True if this tenant still needs onboarding (or has never started)."""
        state = self._repo.get_onboarding_state(tenant_id)
        if state is None:
            return True
        return state.step != "completed"

    async def handle_message(
        self,
        tenant_id: str,
        msg_type: str,
        content: str,
        content_bytes: bytes | None = None,
        mime_type: str = "image/jpeg",
    ) -> list[dict[str, Any]]:
        """
        Process an incoming message and return LINE message objects to send back.
        `content` is the text body for text messages, or the LINE message ID for
        image/file messages.
        """
        state = self._repo.get_or_create_onboarding_state(tenant_id)
        step = state.step

        if step == "new":
            return await self._handle_new(tenant_id)
        elif step == "asking_name":
            return await self._handle_asking_name(tenant_id, content)
        elif step == "asking_industry":
            return await self._handle_asking_industry(tenant_id, content)
        elif step == "asking_address":
            return await self._handle_asking_address(tenant_id, content)
        elif step == "awaiting_docs":
            return await self._handle_awaiting_docs(tenant_id, msg_type, content, content_bytes, mime_type)
        elif step == "interview_q1":
            return await self._handle_interview_q1(tenant_id, content)
        elif step == "interview_q2":
            return await self._handle_interview_q2(tenant_id, content)
        elif step == "interview_q3":
            return await self._handle_interview_q3(tenant_id, content)
        else:
            return []

    # ── Step handlers ─────────────────────────────────────────────────────────

    async def _handle_new(self, tenant_id: str) -> list[dict[str, Any]]:
        self._repo.get_or_create_tenant(tenant_id)
        self._repo.update_onboarding_state(tenant_id, "asking_name")
        return [_text(_BOT_MESSAGES["welcome"])]

    async def _handle_asking_name(
        self, tenant_id: str, content: str
    ) -> list[dict[str, Any]]:
        name = content.strip()
        tenant = self._repo.get_or_create_tenant(tenant_id)
        tenant.name = name
        self._repo.save_tenant(tenant)
        self._repo.save_conversation(
            tenant_id=tenant_id, role="boss", content=content, conversation_type="onboarding"
        )
        self._repo.update_onboarding_state(tenant_id, "asking_industry")
        return [_text(_BOT_MESSAGES["asking_industry"].format(name=name))]

    async def _handle_asking_industry(
        self, tenant_id: str, content: str
    ) -> list[dict[str, Any]]:
        tenant = self._repo.get_or_create_tenant(tenant_id)
        tenant.industry_type = content.strip()
        self._repo.save_tenant(tenant)
        self._repo.save_conversation(
            tenant_id=tenant_id, role="boss", content=content, conversation_type="onboarding"
        )
        self._repo.update_onboarding_state(tenant_id, "asking_address")
        return [_text(_BOT_MESSAGES["asking_address"])]

    async def _handle_asking_address(
        self, tenant_id: str, content: str
    ) -> list[dict[str, Any]]:
        tenant = self._repo.get_or_create_tenant(tenant_id)
        tenant.address = content.strip()
        self._repo.save_tenant(tenant)
        self._repo.save_conversation(
            tenant_id=tenant_id, role="boss", content=content, conversation_type="onboarding"
        )
        self._repo.update_onboarding_state(tenant_id, "awaiting_docs")
        return [_text(_BOT_MESSAGES["awaiting_docs"])]

    async def _handle_awaiting_docs(
        self, tenant_id: str, msg_type: str, content: str,
        content_bytes: bytes | None = None, mime_type: str = "image/jpeg",
    ) -> list[dict[str, Any]]:
        if content.strip().lower() in _SKIP_KEYWORDS:
            self._repo.update_onboarding_state(tenant_id, "interview_q1")
            return [_text(_BOT_MESSAGES["interview_q1"])]

        if msg_type in ("image", "file", "video", "audio"):
            return await self._knowledge_capture.capture_document_input(
                tenant_id=tenant_id,
                msg_type=msg_type,
                content=content,
                content_bytes=content_bytes,
                mime_type=mime_type,
                ack_text=_BOT_MESSAGES["doc_received"],
            )

        # Text while awaiting docs — treat as additional knowledge
        if msg_type == "text" and content.strip():
            return await self._knowledge_capture.capture_document_input(
                tenant_id=tenant_id,
                msg_type=msg_type,
                content=content,
                content_bytes=content_bytes,
                mime_type=mime_type,
                ack_text=_BOT_MESSAGES["doc_received"],
            )

        return [_text(_BOT_MESSAGES["awaiting_docs"])]

    async def _handle_interview_q1(
        self, tenant_id: str, content: str
    ) -> list[dict[str, Any]]:
        self._repo.save_conversation(
            tenant_id=tenant_id, role="boss", content=content, conversation_type="onboarding"
        )
        await self._store_knowledge(
            tenant_id=tenant_id,
            category="core_value",
            content=content.strip(),
            source_type="conversation",
        )
        self._repo.update_onboarding_state(tenant_id, "interview_q2")
        return [_text(_BOT_MESSAGES["interview_q2"])]

    async def _handle_interview_q2(
        self, tenant_id: str, content: str
    ) -> list[dict[str, Any]]:
        redo_step = _detect_redo_step(content, current_step="interview_q2")
        if redo_step:
            # Delete previously saved core_value entries and roll back
            for entry in self._repo.get_knowledge_entries(tenant_id, category=_STEP_CATEGORY.get(redo_step, "")):
                self._repo.delete_knowledge_entry(entry.id)
            self._repo.update_onboarding_state(tenant_id, redo_step)
            return [_text("沒問題！讓我們重新來 ✍️\n\n" + _BOT_MESSAGES[redo_step])]
        self._repo.save_conversation(
            tenant_id=tenant_id, role="boss", content=content, conversation_type="onboarding"
        )
        await self._store_knowledge(
            tenant_id=tenant_id,
            category="pain_point",
            content=content.strip(),
            source_type="conversation",
        )
        self._repo.update_onboarding_state(tenant_id, "interview_q3")
        return [_text(_BOT_MESSAGES["interview_q3"])]

    async def _handle_interview_q3(
        self, tenant_id: str, content: str
    ) -> list[dict[str, Any]]:
        redo_step = _detect_redo_step(content, current_step="interview_q3")
        if redo_step:
            # Delete previously saved knowledge for the target step and beyond
            for category in [_STEP_CATEGORY[s] for s in ("interview_q1", "interview_q2") if _STEP_CATEGORY.get(s) and (redo_step == "interview_q1" or s == "interview_q2")]:
                for entry in self._repo.get_knowledge_entries(tenant_id, category=category):
                    self._repo.delete_knowledge_entry(entry.id)
            self._repo.update_onboarding_state(tenant_id, redo_step)
            return [_text("沒問題！讓我們重新來 ✍️\n\n" + _BOT_MESSAGES[redo_step])]
        self._repo.save_conversation(
            tenant_id=tenant_id, role="boss", content=content, conversation_type="onboarding"
        )
        await self._store_knowledge(
            tenant_id=tenant_id,
            category="goal",
            content=content.strip(),
            source_type="conversation",
        )

        # Summarise basic info into a knowledge entry for easy RAG retrieval
        tenant = self._repo.get_or_create_tenant(tenant_id)
        basic_info = (
            f"店名：{tenant.name}，行業：{tenant.industry_type}，地址：{tenant.address}"
        )
        await self._store_knowledge(
            tenant_id=tenant_id,
            category="basic_info",
            content=basic_info,
            source_type="conversation",
        )

        self._repo.update_onboarding_state(tenant_id, "completed")
        if self._context_brief_manager is not None:
            try:
                await self._context_brief_manager.refresh_briefs(
                    tenant_id,
                    reason="onboarding_completed",
                )
            except Exception as exc:
                logger.warning("brief refresh failed after onboarding completion: %s", exc)
        await self._dispatch_onboarding_aha(tenant_id, tenant.name, tenant.industry_type)
        absorption = self._knowledge_capture.build_absorption_summary_text(tenant_id)
        messages = []
        if absorption:
            messages.append(_text(absorption))
        readiness = self._build_readiness_summary_text(tenant_id)
        if readiness:
            messages.append(_text(readiness))
        messages.append(_text(_BOT_MESSAGES["completed"]))
        return messages

    async def _store_knowledge(
        self,
        *,
        tenant_id: str,
        category: str,
        content: str,
        source_type: str,
        source_id: str | None = None,
    ) -> None:
        if self._memory is not None:
            await self._memory.store_knowledge(
                tenant_id=tenant_id,
                category=category,
                content=content,
                source_type=source_type,
                source_id=source_id,
            )
            return
        self._repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category=category,
            content=content,
            source_type=source_type,
            source_id=source_id,
        )

    def _build_doc_absorption_messages(self, tenant_id: str) -> list[dict[str, Any]]:
        return self._knowledge_capture.build_absorption_messages(
            tenant_id,
            ack_text=_BOT_MESSAGES["doc_received"],
        )

    def _build_absorption_summary_text(self, tenant_id: str) -> str:
        return self._knowledge_capture.build_absorption_summary_text(tenant_id)

    def _build_readiness_summary_text(self, tenant_id: str) -> str:
        if not hasattr(self._repo, "get_connector_account"):
            return ""
        try:
            from ..auth.oauth import _build_phase0_readiness_lines

            return "\n".join(_build_phase0_readiness_lines(self._repo, tenant_id))
        except Exception as exc:
            logger.warning("failed to build readiness summary for tenant=%s: %s", tenant_id, exc)
            return ""

    async def _dispatch_onboarding_aha(
        self,
        tenant_id: str,
        tenant_name: str,
        industry_type: str,
    ) -> None:
        if self._intent_router is None:
            return

        from ..models import Intent

        brand = (tenant_name or "你的品牌").strip()
        industry = (industry_type or "服務").strip()
        for index, template in enumerate(_ONBOARDING_AHA_TOPIC_TEMPLATES, start=1):
            topic = template.format(brand=brand, industry=industry)
            try:
                await self._intent_router.dispatch(
                    intent=Intent.GOOGLE_POST,
                    tenant_id=tenant_id,
                    trigger_source="onboarding_aha",
                    trigger_payload={
                        "topic": topic,
                        "message": topic,
                        "sequence": index,
                    },
                )
            except Exception as exc:  # pragma: no cover - defensive; onboarding must still complete
                logger.error(
                    "Failed to dispatch onboarding aha draft %s for tenant=%s: %s",
                    index,
                    tenant_id,
                    exc,
                )
