from __future__ import annotations

import logging
from typing import Any

from . import document_parser
from .conversation_context import (
    extract_document_contact_facts,
    extract_document_offer_facts,
    extract_document_product_facts,
    extract_document_restriction_facts,
    extract_document_style_facts,
    is_low_signal_document_text,
)

logger = logging.getLogger(__name__)


def _text_message(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


class KnowledgeCaptureService:
    """Stores boss-provided brand information and returns absorption feedback."""

    def __init__(
        self,
        repo,
        settings,
        memory_manager=None,
        context_brief_manager=None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._memory = memory_manager
        self._context_brief_manager = context_brief_manager

    async def capture_document_input(
        self,
        *,
        tenant_id: str,
        msg_type: str,
        content: str,
        content_bytes: bytes | None = None,
        mime_type: str = "image/jpeg",
        ack_text: str = "我先把這份資料收進品牌知識庫了。",
    ) -> list[dict[str, Any]]:
        if msg_type in ("image", "file", "video", "audio"):
            if self._settings is not None and (content_bytes or msg_type == "audio"):
                result = await document_parser.parse_document(
                    msg_type=msg_type,
                    content_bytes=content_bytes,
                    content_text=None,
                    mime_type=mime_type,
                    settings=self._settings,
                )
                if result.needs_manual:
                    logger.warning(
                        "Document capture needs_manual: tenant=%s type=%s error=%s",
                        tenant_id,
                        msg_type,
                        result.error,
                    )
                    return [
                        _text_message(
                            "⚠️ 已收到，但這份檔案暫時無法自動解析。\n"
                            "我已記錄下來，建議之後手動整理後再傳一次。"
                        )
                    ]
                return await self.capture_knowledge_text(
                    tenant_id=tenant_id,
                    content=result.text,
                    source_type=result.source_type,
                    source_id=content,
                    ack_text=ack_text,
                )
            else:
                return await self.capture_knowledge_text(
                    tenant_id=tenant_id,
                    content=f"[{msg_type} uploaded, message_id={content}]",
                    source_type="document",
                    source_id=content,
                    ack_text=ack_text,
                )

        if msg_type == "text" and content.strip():
            return await self.capture_knowledge_text(
                tenant_id=tenant_id,
                content=content.strip(),
                source_type="text",
                ack_text=ack_text,
            )

        return [_text_message("我先記下來了，但這則內容目前還不足以整理成品牌資料。")]

    async def capture_knowledge_text(
        self,
        *,
        tenant_id: str,
        content: str,
        source_type: str,
        source_id: str | None = None,
        ack_text: str = "我先把這份資料收進品牌知識庫了。",
    ) -> list[dict[str, Any]]:
        await self.store_knowledge(
            tenant_id=tenant_id,
            category="document",
            content=content,
            source_type=source_type,
            source_id=source_id,
        )
        self._store_derived_document_facts(
            tenant_id=tenant_id,
            content=content,
            source_type=source_type,
            source_id=source_id,
        )
        await self.refresh_briefs(tenant_id, reason="knowledge_capture")
        return self.build_absorption_messages(tenant_id, ack_text=ack_text)

    async def store_knowledge(
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

    def build_absorption_messages(
        self,
        tenant_id: str,
        *,
        ack_text: str,
    ) -> list[dict[str, Any]]:
        messages = [_text_message(ack_text)]
        absorption = self.build_absorption_summary_text(tenant_id)
        if absorption:
            messages.append(_text_message(absorption))
        return messages

    def build_absorption_summary_text(self, tenant_id: str) -> str:
        if not hasattr(self._repo, "get_knowledge_entries"):
            return ""

        tenant = self._repo.get_or_create_tenant(tenant_id)
        document_entries = self._repo.get_knowledge_entries(tenant_id, category="document")
        core_values = self._repo.get_knowledge_entries(tenant_id, category="core_value")
        goals = self._repo.get_knowledge_entries(tenant_id, category="goal")

        bullets: list[str] = []
        identity = " / ".join(
            part for part in [tenant.name, tenant.industry_type] if part
        )
        if identity:
            bullets.append(f"品牌輪廓：{identity}")
        if document_entries:
            bullets.append(f"已讀取 {len(document_entries)} 份品牌資料")
        if core_values:
            bullets.append(f"你最想被記住的是：{core_values[-1].content[:40]}")
        if goals:
            bullets.append(f"你目前最想先做到：{goals[-1].content[:40]}")

        if not bullets:
            return ""

        return (
            "我目前已先吸收這些資訊：\n"
            + "\n".join(f"• {bullet}" for bullet in bullets)
            + "\n\n之後我會把這些資訊用在文案、建議和後續對話。"
        )

    def _store_derived_document_facts(
        self,
        *,
        tenant_id: str,
        content: str,
        source_type: str,
        source_id: str | None,
    ) -> None:
        if not content or is_low_signal_document_text(content):
            return
        if not hasattr(self._repo, "get_active_knowledge_entries"):
            return

        derived_facts = {
            "product": extract_document_product_facts(content, max_items=4),
            "contact": extract_document_contact_facts(content, max_items=4),
            "style": extract_document_style_facts(content, max_items=2),
            "offer": extract_document_offer_facts(content, max_items=3),
            "restriction": extract_document_restriction_facts(content, max_items=3),
        }
        existing_by_category = {
            category: {
                entry.content
                for entry in self._repo.get_active_knowledge_entries(tenant_id, categories=[category])
            }
            for category in derived_facts
        }
        for category, facts in derived_facts.items():
            for fact in facts:
                if fact in existing_by_category[category]:
                    continue
                self._repo.save_knowledge_entry(
                    tenant_id=tenant_id,
                    category=category,
                    content=fact,
                    source_type=f"{source_type}_derived",
                    source_id=source_id,
                )
                existing_by_category[category].add(fact)

    async def refresh_briefs(self, tenant_id: str, *, reason: str) -> None:
        if self._context_brief_manager is None:
            return
        try:
            await self._context_brief_manager.refresh_briefs(tenant_id, reason=reason)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("brief refresh failed for tenant=%s reason=%s: %s", tenant_id, reason, exc)