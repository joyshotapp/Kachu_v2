from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..persistence import KachuRepository

if TYPE_CHECKING:
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
        "🎉 太好了！我已經了解你的生意了。\n\n"
        "接下來我會先幫你準備 3 篇可直接審稿的 Google 草稿，你只需要確認。\n\n"
        "試試看，傳一張你想發布的照片給我 📸"
    ),
}

_SKIP_KEYWORDS = {"完成", "好了", "done", "跳過", "skip", "next"}
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
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._intent_router = intent_router

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
            if self._settings is not None and (content_bytes or msg_type == "audio"):
                # Real parsing pipeline
                from ..document_parser import parse_document
                result = await parse_document(
                    msg_type=msg_type,
                    content_bytes=content_bytes,
                    content_text=None,
                    mime_type=mime_type,
                    settings=self._settings,
                )
                if result.needs_manual:
                    logger.warning(
                        "Document parse needs_manual: tenant=%s type=%s error=%s",
                        tenant_id, msg_type, result.error,
                    )
                    return [
                        _text(
                            "⚠️ 已收到，但這份檔案暫時無法自動解析。\n"
                            "我已記錄下來，建議之後手動整理後再傳一次 📂"
                        )
                    ]

                # Store parsed content as knowledge entry
                self._repo.save_knowledge_entry(
                    tenant_id=tenant_id,
                    category="document",
                    content=result.text,
                    source_type=result.source_type,
                    source_id=content,
                )
                logger.info(
                    "Document parsed and stored: tenant=%s source=%s confidence=%.2f",
                    tenant_id, result.source_type, result.confidence,
                )
            else:
                # Fallback: no settings or no bytes — store placeholder
                self._repo.save_knowledge_entry(
                    tenant_id=tenant_id,
                    category="document",
                    content=f"[{msg_type} uploaded, message_id={content}]",
                    source_type="document",
                    source_id=content,
                )
            return [_text(_BOT_MESSAGES["doc_received"])]

        # Text while awaiting docs — treat as additional knowledge
        if msg_type == "text" and content.strip():
            self._repo.save_knowledge_entry(
                tenant_id=tenant_id,
                category="document",
                content=content.strip(),
                source_type="text",
            )
            return [_text(_BOT_MESSAGES["doc_received"])]

        return [_text(_BOT_MESSAGES["awaiting_docs"])]

    async def _handle_interview_q1(
        self, tenant_id: str, content: str
    ) -> list[dict[str, Any]]:
        self._repo.save_conversation(
            tenant_id=tenant_id, role="boss", content=content, conversation_type="onboarding"
        )
        self._repo.save_knowledge_entry(
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
        self._repo.save_conversation(
            tenant_id=tenant_id, role="boss", content=content, conversation_type="onboarding"
        )
        self._repo.save_knowledge_entry(
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
        self._repo.save_conversation(
            tenant_id=tenant_id, role="boss", content=content, conversation_type="onboarding"
        )
        self._repo.save_knowledge_entry(
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
        self._repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category="basic_info",
            content=basic_info,
            source_type="conversation",
        )

        self._repo.update_onboarding_state(tenant_id, "completed")
        await self._dispatch_onboarding_aha(tenant_id, tenant.name, tenant.industry_type)
        return [_text(_BOT_MESSAGES["completed"])]

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
