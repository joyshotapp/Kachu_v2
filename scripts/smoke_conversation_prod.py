#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOST = "root@172.234.85.159"
DEFAULT_TENANT_ID = "3161e00e-0eec-4070-872d-d15b1fb859bc"
DEFAULT_LINE_USER_ID = "U1f7215a15f956a462bd196b19cc30f87"
DEFAULT_CONTAINER = "kachu-plus-kachu-plus-1"


@dataclass(frozen=True)
class SmokeCase:
    label: str
    text: str
    expected_mode: str
    expected_strategy: str
    expected_reply_hint: str


CASES: tuple[SmokeCase, ...] = (
    SmokeCase("greeting", "你好", "consult", "greeting", "你好，我在"),
    SmokeCase("capability", "你能做什麼？", "consult", "capability_overview", "我可以直接幫你做幾件事"),
    SmokeCase("consult", "你覺得我接下來該先衝來客還是先衝回購？", "consult", "consult_llm", "我建議"),
    SmokeCase("clarify", "最近流量掉很多", "clarify", "ask_targeted_question", "拉報告看數字"),
    SmokeCase("review", "有個評論", "clarify", "ask_targeted_question", "直接幫你回這則評論"),
    SmokeCase("customer", "最近很多老客都沒回來", "clarify", "ask_targeted_question", "先找出哪些客人變少或沒回來"),
    SmokeCase("content", "我想做母親節，但不知道怎麼切角度", "consult", "consult_llm", "方向"),
    SmokeCase("store_profile", "明天提早打烊", "execute", "execute", "execute_ack"),
    SmokeCase("emotion", "最近生意很差，我有點焦慮", "clarify", "empathy_clarify", "我知道你現在有點擔心"),
)


REMOTE_SCRIPT = r'''
import asyncio
import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, create_engine, select

from kachu_plus.config import get_settings
from kachu_plus.crypto import decrypt_field
from kachu_plus.persistence.tables import ConversationTable, LineChannelConfigTable

TENANT_ID = __TENANT_ID__
LINE_USER_ID = __LINE_USER_ID__
CASES = __CASES__

settings = get_settings()
engine = create_engine(settings.DATABASE_URL)

with Session(engine) as session:
    config = session.exec(
        select(LineChannelConfigTable).where(
            LineChannelConfigTable.tenant_id == TENANT_ID,
            LineChannelConfigTable.is_active == True,
        )
    ).first()
    if config is None:
        raise SystemExit(f'missing active line channel config for tenant {TENANT_ID}')
    secret = decrypt_field(config.channel_secret, settings.FIELD_ENCRYPTION_KEY)


async def run_case(client, label, text, expected_mode, expected_strategy, expected_reply_hint):
    message_id = f'smoke-{label}-{uuid.uuid4().hex[:8]}'
    body = {
        'destination': config.channel_id or 'smoke',
        'events': [{
            'type': 'message',
            'timestamp': int(datetime.now(timezone.utc).timestamp() * 1000),
            'source': {'type': 'user', 'userId': LINE_USER_ID},
            'replyToken': f'reply-{uuid.uuid4().hex[:8]}',
            'mode': 'active',
            'message': {'id': message_id, 'type': 'text', 'text': text},
        }],
    }
    payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
    signature = base64.b64encode(hmac.new(secret.encode(), payload, hashlib.sha256).digest()).decode()
    response = await client.post(
        f'https://plus.kachu.tw/webhooks/line/{TENANT_ID}',
        content=payload,
        headers={'X-Line-Signature': signature, 'Content-Type': 'application/json'},
    )
    await asyncio.sleep(2.0)

    with Session(engine) as session:
        boss_rows = session.exec(
            select(ConversationTable)
            .where(ConversationTable.tenant_id == TENANT_ID)
            .where(ConversationTable.line_user_id == LINE_USER_ID)
            .where(ConversationTable.actor_role == 'boss')
            .order_by(ConversationTable.created_at.desc())
            .limit(12)
        ).all()
    boss_row = next((row for row in boss_rows if getattr(row, 'source_message_id', '') == message_id), None)
    ai_preview = ''
    ai_kind = ''
    if boss_row is not None:
        with Session(engine) as session:
            ai_rows = session.exec(
                select(ConversationTable)
                .where(ConversationTable.tenant_id == TENANT_ID)
                .where(ConversationTable.line_user_id == LINE_USER_ID)
                .where(ConversationTable.actor_role.in_(['ai', 'system']))
                .where(ConversationTable.created_at >= boss_row.created_at)
                .order_by(ConversationTable.created_at.asc())
                .limit(6)
            ).all()
        for row in ai_rows:
            preview = (row.content_text or '').replace('\n', ' / ')
            if expected_reply_hint in preview:
                ai_preview = preview[:260]
                ai_kind = row.conversation_kind or ''
                break
            if not ai_preview and row.conversation_kind in {'boss_consult', 'follow_up', 'execute_ack', 'execute_result'}:
                ai_preview = preview[:260]
                ai_kind = row.conversation_kind or ''

    metadata = json.loads(boss_row.metadata_json or '{}') if boss_row and boss_row.metadata_json else {}
    ok = (
        response.status_code == 200
        and metadata.get('mode') == expected_mode
        and metadata.get('response_strategy') == expected_strategy
        and (
            expected_reply_hint in ai_preview
            or expected_reply_hint == ai_kind
            or (expected_reply_hint == 'execute_ack' and ai_kind == 'execute_ack')
        )
    )

    print(f'[{"PASS" if ok else "FAIL"}] {label}')
    print(f'  text={text}')
    print(f'  http={response.status_code}')
    print(f'  mode={metadata.get("mode", "")} strategy={metadata.get("response_strategy", "")} conf={metadata.get("planner_confidence", "")}')
    print(f'  reply={ai_preview or "(missing)"}')
    print('')
    return ok


async def main():
    failed = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        for case in CASES:
            ok = await run_case(client, *case)
            if not ok:
                failed += 1
    if failed:
        raise SystemExit(failed)


asyncio.run(main())
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run production conversation smoke/regression checks.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="SSH host")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID, help="Tenant id to exercise")
    parser.add_argument("--line-user-id", default=DEFAULT_LINE_USER_ID, help="Owner LINE user id used for smoke")
    parser.add_argument("--container", default=DEFAULT_CONTAINER, help="Production kachu-plus container name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    remote_script = (
        REMOTE_SCRIPT
        .replace("__TENANT_ID__", repr(args.tenant_id))
        .replace("__LINE_USER_ID__", repr(args.line_user_id))
        .replace("__CASES__", repr([(c.label, c.text, c.expected_mode, c.expected_strategy, c.expected_reply_hint) for c in CASES]))
    )
    command = [
        "ssh",
        args.host,
        f"docker exec -i {args.container} python -",
    ]
    completed = subprocess.run(command, input=remote_script, text=True, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())