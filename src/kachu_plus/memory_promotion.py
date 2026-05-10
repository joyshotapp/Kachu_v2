from __future__ import annotations

from typing import Any


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _platform_from_text(text: str) -> str:
    if _contains_any(text, ("fb", "facebook", "粉專", "meta", "ig", "instagram")):
        return "ig_fb"
    if _contains_any(text, ("line", "line官方", "官方line")):
        return "line"
    return "google"


def _infer_knowledge_category(text: str) -> str:
    if _contains_any(text, ("營業", "公休", "地址", "電話", "預約", "店休", "打烊")):
        return "basic_info"
    if _contains_any(text, ("價格", "價位", "售價", "優惠", "折扣", "方案")):
        return "offer"
    if _contains_any(text, ("菜單", "服務", "品項", "新品", "套餐", "商品")):
        return "product"
    if _contains_any(text, ("理念", "主打", "特色", "堅持", "品牌", "定位")):
        return "core_value"
    if _contains_any(text, ("目標", "希望", "想要", "今年", "本月")):
        return "goal"
    if _contains_any(text, ("困擾", "痛點", "卡住", "抱怨", "問題")):
        return "pain_point"
    return ""


class ConversationMemoryPromoter:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def promote_conversation(
        self,
        *,
        tenant_id: str,
        conversation: Any,
        latest_task: Any = None,
    ) -> dict[str, Any]:
        actor_role = str(getattr(conversation, "actor_role", "") or "")
        conversation_kind = str(getattr(conversation, "conversation_kind", "") or "")
        content_text = str(getattr(conversation, "content_text", "") or "").strip()
        if actor_role != "boss" or not content_text:
            return {"knowledge": 0, "preferences": 0, "episodes": 0}

        promoted_knowledge = 0
        promoted_preferences = 0
        promoted_episodes = 0

        if conversation_kind != "onboarding":
            category = _infer_knowledge_category(content_text)
            if category and not self._has_matching_knowledge(tenant_id=tenant_id, category=category, content_text=content_text):
                self._repo.save_knowledge_entry(
                    tenant_id=tenant_id,
                    category=category,
                    content=content_text,
                    source_conversation_id=str(getattr(conversation, "id", "") or ""),
                    confidence_score=0.72,
                )
                promoted_knowledge += 1

        if _contains_any(content_text, ("語氣", "口吻", "風格", "不要", "改成", "CTA", "hashtag", "表情符號", "emoji")):
            if not self._has_matching_preference(tenant_id=tenant_id, platform=_platform_from_text(content_text), content_text=content_text):
                self._repo.save_preference_memory(
                    tenant_id=tenant_id,
                    platform=_platform_from_text(content_text),
                    original_draft=content_text,
                    edited_draft=content_text,
                    diff_notes="老闆主動描述偏好",
                    run_id=str(getattr(latest_task, "run_id", "") or ""),
                )
                promoted_preferences += 1

        related_task = latest_task
        if related_task is not None and _contains_any(content_text, ("可以", "發吧", "就這個", "不錯", "好，發", "ok", "OK", "改一下", "重寫", "不要", "太長", "太像")):
            outcome = "accepted" if _contains_any(content_text, ("可以", "發吧", "就這個", "不錯", "好，發", "ok", "OK")) else "needs_revision"
            if not self._has_matching_episode(
                tenant_id=tenant_id,
                workflow_type=str(getattr(related_task, "intent_label", "") or "general_follow_up"),
                outcome=outcome,
            ):
                self._repo.save_episodic_memory(
                    tenant_id=tenant_id,
                    workflow_type=str(getattr(related_task, "intent_label", "") or "general_follow_up"),
                    outcome=outcome,
                    context_summary=(
                        '{"conversation_id": "%s", "task_id": "%s", "signal": "%s"}'
                        % (
                            str(getattr(conversation, "id", "") or ""),
                            str(getattr(related_task, "task_id", "") or ""),
                            content_text.replace('"', '\\"'),
                        )
                    ),
                )
                promoted_episodes += 1

        return {
            "knowledge": promoted_knowledge,
            "preferences": promoted_preferences,
            "episodes": promoted_episodes,
        }

    def _has_matching_knowledge(self, *, tenant_id: str, category: str, content_text: str) -> bool:
        recent = self._repo.list_knowledge_entries(tenant_id, limit=30)
        normalized = content_text.strip()
        return any(str(getattr(row, "category", "") or "") == category and str(getattr(row, "content", "") or "").strip() == normalized for row in recent)

    def _has_matching_preference(self, *, tenant_id: str, platform: str, content_text: str) -> bool:
        recent = self._repo.get_preference_memories(tenant_id, platform=platform, limit=10)
        normalized = content_text.strip()
        return any(str(getattr(row, "edited_draft", "") or "").strip() == normalized for row in recent)

    def _has_matching_episode(self, *, tenant_id: str, workflow_type: str, outcome: str) -> bool:
        recent = self._repo.get_episodic_memories(tenant_id, workflow_type=workflow_type, limit=5)
        return any(str(getattr(row, "outcome", "") or "") == outcome for row in recent)