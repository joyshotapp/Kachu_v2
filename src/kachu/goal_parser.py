"""
Phase 5: GoalParser

Handles `general_chat` intent — when the boss says something that does not
match any of the six known intents, instead of replying "不理解" the agent
performs a goal analysis and proposes a concrete action list.

This is the first implementation of goal-driven intent parsing in Kachu.

Flow:
  1. LLM identifies the domain of the boss's concern
     (traffic / reputation / content / knowledge / operations)
  2. Maps domain → list of relevant workflow actions
  3. Returns LINE Flex Message with action buttons for the boss to choose
  4. Boss taps a button → IntentRouter dispatches the selected workflow
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .llm import generate_text

logger = logging.getLogger(__name__)

_INTENT_TO_WORKFLOW = {
    "ga4_report": "kachu_ga4_report",
    "google_post": "kachu_google_post",
    "knowledge_update": "kachu_knowledge_update",
    "review_reply": "kachu_review_reply",
}

# ── Goal domain → suggested actions mapping ───────────────────────────────────

_DOMAIN_ACTIONS: dict[str, list[dict[str, str]]] = {
    "traffic": [
        {"label": "查看本週流量報告", "intent": "ga4_report", "topic": ""},
        {"label": "發一篇 Google 商家動態", "intent": "google_post", "topic": "提升能見度"},
    ],
    "reputation": [
        {"label": "查看待回覆的顧客評論", "intent": "review_reply", "topic": ""},
        {"label": "更新知識庫中的常見問答", "intent": "knowledge_update", "topic": "FAQ更新"},
    ],
    "content": [
        {"label": "幫我寫一篇 IG/FB 貼文", "intent": "google_post", "topic": "內容行銷"},
        {"label": "發一篇 Google 商家動態", "intent": "google_post", "topic": ""},
    ],
    "knowledge": [
        {"label": "更新店家資訊", "intent": "knowledge_update", "topic": "資訊更新"},
        {"label": "查看本週 GA4 報告", "intent": "ga4_report", "topic": ""},
    ],
    "operations": [
        {"label": "更新產品或價格資訊", "intent": "knowledge_update", "topic": "價格更新"},
        {"label": "查看待確認的任務", "intent": "ga4_report", "topic": ""},
    ],
}

_DEFAULT_DOMAIN = "content"

_CLASSIFY_PROMPT = """\
老闆說了：「{message}」

請判斷老闆關心的是哪個領域（只回一個詞）：
- traffic（網站流量、訪客、業績數字）
- reputation（評論、評價、口碑）
- content（發文、貼文、照片、宣傳）
- knowledge（菜單、價格、店家資訊、FAQ）
- operations（日常營運、排班、其他）

只回一個英文小寫詞，不要其他說明。"""


class GoalParser:
    """
    Converts open-ended boss messages into actionable workflow proposals.

    Returns a list of suggested actions; callers (webhook handler) are
    responsible for formatting them into LINE Flex Message buttons.
    """

    def __init__(self, settings) -> None:
        self._settings = settings

    async def parse(self, message: str) -> list[dict[str, str]]:
        """
        Analyse the boss's message and return a list of suggested actions.

        Each action: {"label": str, "intent": str, "topic": str}
        """
        domain = await self._classify_domain(message)
        actions = _DOMAIN_ACTIONS.get(domain, _DOMAIN_ACTIONS[_DEFAULT_DOMAIN])
        logger.info("GoalParser: message='%s...' domain=%s actions=%d", message[:40], domain, len(actions))
        return actions

    def build_line_quick_reply(self, actions: list[dict[str, str]]) -> dict[str, Any]:
        """Build a LINE Flex quickReply object from the actions list."""
        items = [
            {
                "type": "action",
                "action": {
                    "type": "postback",
                    "label": a["label"][:20],
                    "data": (
                        f"action=trigger_workflow"
                        f"&workflow={_INTENT_TO_WORKFLOW.get(a['intent'], '')}"
                        f"&intent={a['intent']}"
                        f"&topic={a['topic']}"
                    ),
                    "displayText": a["label"],
                },
            }
            for a in actions[:5]   # LINE max 13 items
        ]
        return {"type": "quickReply", "items": items}

    def build_text_response(self, message: str, actions: list[dict[str, str]]) -> dict[str, Any]:
        """Build a text message with a quick reply for the boss."""
        intro = (
            "我了解你的問題了 😊\n"
            "以下是我建議可以做的事，你點一個我幫你去做："
        )
        quick_reply = self.build_line_quick_reply(actions)
        return {
            "type": "text",
            "text": intro,
            "quickReply": quick_reply if quick_reply["items"] else None,
        }

    async def _classify_domain(self, message: str) -> str:
        """Use LLM to classify the boss's concern domain."""
        if not (self._settings.GOOGLE_AI_API_KEY or self._settings.OPENAI_API_KEY):
            return _DEFAULT_DOMAIN
        try:
            prompt = _CLASSIFY_PROMPT.format(message=message[:200])
            raw = await generate_text(
                prompt=prompt,
                model=self._settings.LITELLM_MODEL,
                api_key=self._settings.GOOGLE_AI_API_KEY,
                openai_api_key=self._settings.OPENAI_API_KEY,
            )
            domain = raw.strip().lower().split()[0] if raw.strip() else _DEFAULT_DOMAIN
            if domain not in _DOMAIN_ACTIONS:
                domain = _DEFAULT_DOMAIN
            return domain
        except (httpx.HTTPError, TimeoutError, ModuleNotFoundError) as exc:
            logger.warning("GoalParser LLM classify failed: %s", exc)
            return _DEFAULT_DOMAIN
