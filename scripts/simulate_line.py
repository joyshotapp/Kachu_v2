"""
Kachu+ LINE 沙盒模擬器
======================
在不消耗 LINE API 額度的情況下，模擬微型店家老闆透過 LINE 與 Kachu+ 互動。

技術基礎：
  • FastAPI TestClient + in-memory SQLite：不需要任何外部服務
  • HMAC-SHA256 自動產生 X-Line-Signature：不需要 LINE token
  • push_line_messages 被攔截印出：等同手機上收到的 LINE 訊息
  • execute_dispatcher.dispatch 用 AsyncMock 模擬 AgentOS dispatch 成功

【已實作、會真正跑到的功能】
  ✓ 圖片上傳 → 用途引導（寫貼文 / 進知識庫 / 先討論）
  ✓ postback 分流到 photo_content / knowledge_update / consult
  ✓ 文字訊息消費尚未決策的 pending asset intent
  ✓ 知識庫存入（save_knowledge_entry 真實呼叫）
  ✓ AI 諮詢路徑（consultant.build_reply；沙盒回傳預設文字）
  ✓ execute dispatch ACK 訊息
  ✓ 草稿追問 follow-up 路徑
  ✓ 意圖路由（intent_router）

【由 Mock 模擬的部分】
  ⚠ AgentOS 真實 task 執行 & draft 產生（模擬 waiting_approval 回傳）
  ⚠ LINE 實際 push（攔截後印出）
  ⚠ AI consultant 回覆（預設文字，無 LLM API key 時使用）
  ⚠ 圖片分析 LLM（fallback 固定描述）

用法：
  python scripts/simulate_line.py                         # 互動模式
  python scripts/simulate_line.py --scenario photo        # 情境 A：傳圖寫貼文
  python scripts/simulate_line.py --scenario knowledge    # 情境 B：傳圖進知識庫
  python scripts/simulate_line.py --scenario consult_img  # 情境 C：傳圖先討論
  python scripts/simulate_line.py --scenario text_asset   # 情境 D：打字消費未決圖片
  python scripts/simulate_line.py --scenario ask          # 情境 E：文字諮詢
  python scripts/simulate_line.py --scenario follow_up    # 情境 F：追問草稿進度
  python scripts/simulate_line.py --scenario all          # 全部情境跑一遍
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "src"))

from kachu_plus.approval import ApprovalBridge
from kachu_plus.config import Settings
from kachu_plus.google_business import GoogleReviewService
from kachu_plus.learning import ContextBriefManager, MemoryManager, PostTaskReviewService
from kachu_plus.line.webhook import router as line_router
from kachu_plus.meta import router as meta_router
from kachu_plus.models import ExecutionTaskResult
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import LineChannelConfigTable, TenantTable
from kachu_plus.services import AgentOSTaskDispatcher
from kachu_plus.tools_router import router as tools_router

# ──────────────────────── ANSI 顏色 ──────────────────────────────
_R = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_MAGENTA = "\033[95m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_R}"


# ──────────────────────── 沙盒常數 ───────────────────────────────
CHANNEL_SECRET = "sim-secret"
TENANT_ID = "sim-tenant"

# 模擬業種：手搖飲料店（台北市大安區，節慶行銷需求高）
SHOP_NAME = "葉葉茶飲"
SHOP_INDUSTRY = "bubble_tea"
SHOP_ADDRESS = "台北市大安區復興南路一段 200 號"

# 老闆的 LINE user ID（模擬）
BOSS_USER_ID = "U-boss-lineleafytea"


# ──────────────────────── Fake 基礎設施 ──────────────────────────

class _FakeGBPClient:
    def __init__(self, **_) -> None:
        pass

    def get_review(self, account_id, location_id, review_id):
        return {
            "reviewId": review_id,
            "starRating": "TWO",
            "comment": "等很久，店員態度還好",
            "reviewer": {"displayName": "陳小姐"},
            "createTime": "2026-05-10T10:00:00Z",
        }

    def post_reply(self, account_id, location_id, review_id, reply_text):
        return {"reviewId": review_id, "comment": reply_text}

    def create_local_post(self, account_id, location_id, summary, call_to_action_url=""):
        return {"name": "localPosts/sim-1", "summary": summary}


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed(repo: KachuPlusRepository) -> None:
    with Session(repo._engine) as session:
        session.add(TenantTable(
            id=TENANT_ID,
            name=SHOP_NAME,
            industry_type=SHOP_INDUSTRY,
            address=SHOP_ADDRESS,
        ))
        session.add(LineChannelConfigTable(
            id=f"cfg-{TENANT_ID}",
            tenant_id=TENANT_ID,
            channel_secret=CHANNEL_SECRET,
            channel_access_token="sim-token",
            channel_id="sim-line-channel",
        ))
        session.commit()


def _make_dispatch_result(intent_label: str) -> ExecutionTaskResult:
    """模擬 AgentOS 成功接收任務並進入 waiting_approval 狀態"""
    return ExecutionTaskResult(
        task_id=f"task-{uuid.uuid4().hex[:8]}",
        domain=f"kachu_{intent_label}",
        status="waiting_approval",
        objective={
            "photo_content": "Generate social post drafts from the boss photo upload",
            "review_reply": "Generate a review reply draft for owner approval",
            "google_post": "Generate a Google Business post draft",
            "knowledge_update": "Update brand knowledge from owner input",
        }.get(intent_label, f"Execute {intent_label}"),
        current_run_id=f"run-{uuid.uuid4().hex[:8]}",
        waiting_approval=True,
        approval_count=1,
    )


async def _fake_consultant_reply(
    *,
    tenant_name: str = "",
    industry_type: str = "",
    message: str = "",
    context_bundle: dict | None = None,
) -> str:
    """
    沙盒版 AI 顧問：無 LLM API key 時回傳預設建議文字。
    生產環境改用 LiteLLM 呼叫 Gemini / OpenAI。
    """
    msg = message
    if "母親節" in msg or "節日" in msg:
        return (
            "母親節快到了，我建議走「感謝媽媽的陪伴」主題。\n"
            "三個方向：\n"
            "1. 推出限定飲品組合，附上手寫感謝卡。\n"
            "2. 發 IG 限時動態「曬出媽媽最愛的一杯」活動。\n"
            "3. 母親節當天消費送小禮。\n"
            "你最想主打哪一個方向？"
        )
    if "流量" in msg or "轉換" in msg or "下滑" in msg:
        return (
            "流量下滑通常有三個原因：發文時機、內容類型、互動頻率。\n"
            "你最近一個月發文頻率和以前比有變化嗎？\n"
            "另外 IG 在你的受眾活躍時段發文，通常可以改善觸及率，"
            "你知道你的受眾大概幾點最活躍嗎？"
        )
    if "圖" in msg or "照片" in msg:
        return (
            "這張圖可以往三個方向用：\n"
            "1. 直接當新品上市主視覺，搭配限時優惠文字。\n"
            "2. 做成「製作過程揭密」系列貼文的其中一張。\n"
            "3. 收進品牌知識庫，之後讓 AI 寫文時自動參考。\n"
            "你這次最想先走哪一個方向？"
        )
    return (
        "謝謝你告訴我這個情況。\n"
        "我建議先確認目前的受眾輪廓，再決定下一步的內容策略。\n"
        "你方便說一下現在店裡的主力客群是哪些人嗎？"
    )


def _build_app(repo: KachuPlusRepository) -> tuple[FastAPI, list[dict]]:
    """
    建立 Kachu+ FastAPI 應用：
      1. 攔截 LINE push（存入 pushed log 印出）
      2. 以 AsyncMock 模擬 AgentOS dispatch
      3. 以 AsyncMock 模擬 AI consultant
    """
    pushed: list[dict] = []

    app = FastAPI()
    settings = Settings()
    settings.LINE_CHANNEL_ACCESS_TOKEN = "sim-push-token"
    app.state.repository = repo
    app.state.settings = settings

    consultant = MagicMock()
    consultant.build_reply = AsyncMock(side_effect=_fake_consultant_reply)
    app.state.consultant = consultant

    memory = MemoryManager(repo, settings)
    briefs = ContextBriefManager(repo, memory)
    post_task_review = PostTaskReviewService(repo, memory, briefs)

    # dispatcher：mock dispatch 讓下游路徑（save_execute_task_record 等）真實執行
    dispatcher = MagicMock(spec=AgentOSTaskDispatcher)
    dispatcher.dispatch = AsyncMock(
        side_effect=lambda *, intent_label, **_: _make_dispatch_result(intent_label)
    )
    # get_task / get_run：用 plain dict 而非 spec'd MagicMock，避免 .task/.run 被
    # 推斷為 AsyncMock 後 .get(...) 回傳未 await 的 coroutine
    _fake_task_view = MagicMock()
    _fake_task_view.task = {"status": "waiting_approval", "current_run_id": "run-sim-001"}
    dispatcher.get_task = AsyncMock(return_value=_fake_task_view)
    _fake_run_view = MagicMock()
    _fake_run_view.run = {"status": "waiting_approval"}
    dispatcher.get_run = AsyncMock(return_value=_fake_run_view)
    app.state.execute_dispatcher = dispatcher
    app.state.approval_bridge = ApprovalBridge(dispatcher, repo, post_task_review)

    app.state.google_review_service = GoogleReviewService(
        repo, settings, client_factory=lambda **kw: _FakeGBPClient(**kw)
    )

    app.include_router(tools_router)
    app.include_router(line_router)
    app.include_router(meta_router)

    # 攔截兩個模組的 push_line_messages
    import kachu_plus.line.webhook as wm
    import kachu_plus.tools_router as tr

    _orig_wm = wm.push_line_messages
    _orig_tr = tr.push_line_messages

    async def _intercept(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages if isinstance(messages, list) else [messages]})

    wm.push_line_messages = _intercept
    tr.push_line_messages = _intercept

    return app, pushed


# ──────────────────────── 簽名 & 傳送工具 ─────────────────────────

def _sign(body: bytes) -> str:
    digest = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class LineSession:
    """封裝 TestClient 與 pushed log，提供 LINE 對話操作方法。"""

    def __init__(self, client: TestClient, pushed: list[dict]):
        self.client = client
        self.pushed = pushed
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"sim-msg-{self._seq:04d}"

    def _post(self, body_dict: dict) -> None:
        body = json.dumps(body_dict, ensure_ascii=False).encode()
        sig = _sign(body)
        r = self.client.post(
            f"/webhooks/line/{TENANT_ID}",
            content=body,
            headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
        )
        if r.status_code != 200:
            print(_c(_RED, f"  ⚠ HTTP {r.status_code}: {r.text[:300]}"))

    def send_text(self, text: str, user_id: str = BOSS_USER_ID) -> None:
        _print_boss_action(f"傳文字訊息：{_c(_CYAN, repr(text))}")
        self._post({
            "events": [{
                "type": "message",
                "source": {"type": "user", "userId": user_id},
                "message": {"id": self._next_id(), "type": "text", "text": text},
            }]
        })
        self._flush()

    def send_image(self, user_id: str = BOSS_USER_ID) -> None:
        _print_boss_action("傳圖片（模擬 JPEG bytes）")
        msg_id = self._next_id()

        import kachu_plus.line.webhook as wm
        _orig = wm._download_line_message_content

        async def _fake_dl(line_message_id: str, access_token: str) -> bytes:
            return b"\xff\xd8\xff\xe0" + b"\x00" * 512  # 最小 JPEG，分析器有 fallback

        wm._download_line_message_content = _fake_dl
        try:
            self._post({
                "events": [{
                    "type": "message",
                    "source": {"type": "user", "userId": user_id},
                    "message": {"id": msg_id, "type": "image"},
                }]
            })
        finally:
            wm._download_line_message_content = _orig

        self._flush()

    def send_postback(self, data: str, user_id: str = BOSS_USER_ID) -> None:
        _print_boss_action(f"按快捷按鈕：{_c(_CYAN, data)}")
        self._post({
            "events": [{
                "type": "postback",
                "source": {"type": "user", "userId": user_id},
                "postback": {"data": data},
            }]
        })
        self._flush()

    def _flush(self) -> None:
        if not self.pushed:
            print(_c(_DIM, "  （Kachu+ 沒有 LINE 回應，可能是背景任務派工中）"))
        for push in self.pushed:
            _print_push(push)
        self.pushed.clear()

    def repo(self) -> KachuPlusRepository:
        return self.client.app.state.repository

    def get_pending_asset(self, user_id: str = BOSS_USER_ID):
        return self.repo().get_latest_pending_asset_intent(
            tenant_id=TENANT_ID, line_user_id=user_id
        )


# ──────────────────────── 顯示格式化 ─────────────────────────────

def _print_boss_action(desc: str) -> None:
    print(f"\n{_c(_BOLD, '👤 老闆')}: {desc}")


def _render_msg(msg: dict, pad: str = "  ") -> str:
    mtype = msg.get("type", "?")
    lines = []

    if mtype == "text":
        text = msg.get("text", "")
        lines.append(f"{pad}{_c(_GREEN, '【文字】')}")
        for line in text.split("\n"):
            lines.append(f"{pad}  {line}")
        qr_items = msg.get("quickReply", {}).get("items", [])
        if qr_items:
            labels = [item["action"]["label"] for item in qr_items]
            lines.append(f"{pad}  {_c(_CYAN, '快捷選項：')}{'  |  '.join(_c(_BOLD, lb) for lb in labels)}")

    elif mtype == "flex":
        alt = msg.get("altText", "(Flex 卡片)")
        contents = msg.get("contents", {})
        ctype = contents.get("type", "?")
        lines.append(f"{pad}{_c(_MAGENTA, '【Flex 卡片】')} {alt}")
        if ctype == "bubble":
            for c in contents.get("header", {}).get("contents", []):
                if c.get("text"):
                    lines.append(f"{pad}  標題：{c['text']}")
            for c in contents.get("body", {}).get("contents", []):
                t = c.get("text", "")
                if t:
                    lines.append(f"{pad}  ├ {t}")
                for sub in c.get("contents", []):
                    if sub.get("text"):
                        lines.append(f"{pad}  │  {sub['text']}")
            btns = []
            for c in contents.get("footer", {}).get("contents", []):
                label = c.get("action", {}).get("label", "") or c.get("text", "")
                if label:
                    btns.append(label)
            if btns:
                lines.append(f"{pad}  {_c(_CYAN, '操作按鈕：')}{'  |  '.join(_c(_BOLD, b) for b in btns)}")
        elif ctype == "carousel":
            lines.append(f"{pad}  （包含 {len(contents.get('contents', []))} 張卡片）")
    else:
        lines.append(f"{pad}[{mtype}] {json.dumps(msg, ensure_ascii=False)[:120]}")

    return "\n".join(lines)


def _print_push(push: dict) -> None:
    to = push["to"]
    msgs = push["messages"]
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"{_c(_BOLD + _YELLOW, '📲 Kachu+  →  LINE 老闆')} {_c(_DIM, to)}")
    print(sep)
    for i, m in enumerate(msgs, 1):
        if len(msgs) > 1:
            print(_c(_DIM, f"  [{i}/{len(msgs)}]"))
        print(_render_msg(m))
    print(sep)


def _section(title: str) -> None:
    print(f"\n\n{'═' * 65}")
    print(_c(_BOLD + _MAGENTA, f"  {title}"))
    print("═" * 65)


# ──────────────────────── 情境腳本 ───────────────────────────────

def scenario_photo_write_post(sess: LineSession) -> None:
    """
    情境 A：老闆傳新品飲料照 → 引導 → 選「寫貼文」→ AgentOS 派工
    ================================================================
    葉葉茶飲剛推出「芒果彩霞」季節限定，老闆拍了商品照，
    想讓 Kachu+ 幫他寫一篇 IG/FB 推廣貼文。

    真實路徑：
      image → analyze_photo_payload → save_pending_asset_intent → push quick reply
      postback(photo_content) → dispatch → save_execute_task_record → ACK
    """
    _section("情境 A：傳新品飲料照 → 選「寫貼文」")
    print(_c(_DIM, "  【背景】老闆剛拍好「芒果彩霞」新品照，想發 IG/FB"))

    sess.send_image()

    intent = sess.get_pending_asset()
    if not intent:
        print(_c(_RED, "  ⚠ 找不到 pending asset intent，圖片引導可能失敗"))
        return

    sess.send_postback(
        f"action=asset_intent&decision=photo_content"
        f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}"
    )
    print(_c(_DIM, "  → AgentOS 收到任務，草稿產生後會透過通知卡片推播給老闆確認"))


def scenario_photo_knowledge(sess: LineSession) -> None:
    """
    情境 B：老闆傳品牌素材圖 → 選「進知識庫」→ 直接儲存
    =======================================================
    老闆傳了一張店面裝潢風格照，想讓 Kachu+ 日後寫文時自動參考品牌調性。

    真實路徑：
      image → analyze → save_pending_asset_intent → push quick reply
      postback(knowledge_update) → save_knowledge_entry → reply
    """
    _section("情境 B：傳品牌素材圖 → 選「進知識庫」")
    print(_c(_DIM, "  【背景】老闆傳店面裝潢照，想讓 AI 記住品牌風格"))

    sess.send_image()

    intent = sess.get_pending_asset()
    if not intent:
        print(_c(_RED, "  ⚠ 找不到 pending asset intent"))
        return

    sess.send_postback(
        f"action=asset_intent&decision=knowledge_update"
        f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}"
    )

    entries = sess.repo().list_knowledge_entries(TENANT_ID, limit=3)
    if entries:
        print(_c(_DIM, f"  → 知識庫已新增，最新條目：{entries[0].content[:60]}…"))


def scenario_photo_consult(sess: LineSession) -> None:
    """
    情境 C：老闆傳戶外活動照 → 選「先討論」→ AI 給方向建議
    ==========================================================
    老闆傳了一張戶外市集活動現場照，不確定要怎麼用，想先聽建議。

    真實路徑：
      image → analyze → save_pending_asset_intent → push quick reply
      postback(consult) → consultant.build_reply → reply
    """
    _section("情境 C：傳戶外活動照 → 選「先討論」")
    print(_c(_DIM, "  【背景】老闆在市集擺攤，拍了照，不確定怎麼用最好"))

    sess.send_image()

    intent = sess.get_pending_asset()
    if not intent:
        print(_c(_RED, "  ⚠ 找不到 pending asset intent"))
        return

    sess.send_postback(
        f"action=asset_intent&decision=consult"
        f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}"
    )


def scenario_text_asset_decision(sess: LineSession) -> None:
    """
    情境 D：先傳圖，沒點按鈕，改打字「寫貼文」→ 文字消費 pending asset
    =====================================================================
    老闆傳了圖後沒點快捷按鈕，過了一下改打字，
    Kachu+ 應該識別這是對上一張圖的決策。

    真實路徑：
      image → save_pending_asset_intent
      text("寫貼文") → _resolve_pending_asset_decision_from_text → dispatch → ACK
    """
    _section("情境 D：先傳圖再打字「寫貼文」（文字消費 pending asset）")
    print(_c(_DIM, "  【背景】老闆傳完圖後沒點按鈕，改直接打字"))

    sess.send_image()  # 取得 quick reply 但老闆沒點

    # 老闆直接打字表達意圖
    sess.send_text("寫貼文")


def scenario_text_consult_holiday(sess: LineSession) -> None:
    """
    情境 E：老闆打字諮詢母親節行銷
    ================================
    老闆直接在 LINE 詢問節慶行銷建議，沒有傳圖。
    走純文字諮詢路徑：intent_router → consult → consultant.build_reply

    真實路徑：
      text("母親節...") → intent_router(consult) → build_bundle → consultant.build_reply
    """
    _section("情境 E：文字諮詢母親節行銷策略")
    print(_c(_DIM, "  【背景】母親節快到了，老闆想知道怎麼做促銷最有效"))

    sess.send_text("母親節快到了，你覺得我們手搖飲料店可以做什麼行銷？")


def scenario_draft_follow_up(sess: LineSession) -> None:
    """
    情境 F：老闆追問草稿進度
    =========================
    老闆之前發起過一個貼文任務，現在來問「草稿好了嗎？」
    Kachu+ 查找進行中任務並回覆狀態。

    真實路徑：
      text("草稿...") → intent_router(draft_status) → _refresh_execute_task_reply
    """
    _section("情境 F：追問草稿進度")
    print(_c(_DIM, "  【背景】老闆剛才發起了貼文任務，現在來追問進度"))

    sess.send_text("草稿好了嗎？")
    sess.send_text("現在是什麼狀況？")


# ──────────────────────── 互動模式 ───────────────────────────────

_INTERACTIVE_HELP = """
指令：
  /image           傳一張模擬圖片（觸發引導流程）
  /photo           傳圖並自動選「寫貼文」
  /knowledge       傳圖並自動選「進知識庫」
  /consult_img     傳圖並自動選「先討論」
  /help            顯示這個說明
  /quit            結束模擬

其他任何輸入都會當成文字訊息傳給 Kachu+。
"""


def interactive_mode(sess: LineSession) -> None:
    print(f"\n{_c(_BOLD, 'Kachu+ LINE 沙盒（互動模式）')}")
    print(f"商家：{SHOP_NAME}  業種：{SHOP_INDUSTRY}")
    print("直接輸入想說的話，或用以下指令操作：")
    print(_INTERACTIVE_HELP)

    while True:
        try:
            raw = input(f"{_c(_BOLD + _YELLOW, '老闆 > ')}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n模擬結束。")
            break

        if not raw:
            continue
        if raw == "/quit":
            print("模擬結束。")
            break
        elif raw == "/help":
            print(_INTERACTIVE_HELP)
        elif raw == "/image":
            sess.send_image()
        elif raw == "/photo":
            sess.send_image()
            intent = sess.get_pending_asset()
            if intent:
                sess.send_postback(
                    f"action=asset_intent&decision=photo_content"
                    f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}"
                )
            else:
                print(_c(_RED, "⚠ 找不到 pending asset intent"))
        elif raw == "/knowledge":
            sess.send_image()
            intent = sess.get_pending_asset()
            if intent:
                sess.send_postback(
                    f"action=asset_intent&decision=knowledge_update"
                    f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}"
                )
        elif raw == "/consult_img":
            sess.send_image()
            intent = sess.get_pending_asset()
            if intent:
                sess.send_postback(
                    f"action=asset_intent&decision=consult"
                    f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}"
                )
        else:
            sess.send_text(raw)


# ──────────────────────── 主程式 ─────────────────────────────────

def _build_session() -> LineSession:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.update_onboarding_step(TENANT_ID, "completed")

    app, pushed = _build_app(repo)
    client = TestClient(app, raise_server_exceptions=False)
    return LineSession(client, pushed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kachu+ LINE 沙盒模擬器")
    parser.add_argument(
        "--scenario",
        choices=[
            "photo", "knowledge", "consult_img",
            "text_asset", "ask", "follow_up",
            "all", "interactive",
        ],
        default="interactive",
        help="要執行的情境（預設：interactive 互動模式）",
    )
    args = parser.parse_args()

    sess = _build_session()

    if args.scenario == "interactive":
        interactive_mode(sess)
    elif args.scenario == "photo":
        scenario_photo_write_post(sess)
    elif args.scenario == "knowledge":
        scenario_photo_knowledge(sess)
    elif args.scenario == "consult_img":
        scenario_photo_consult(sess)
    elif args.scenario == "text_asset":
        scenario_text_asset_decision(sess)
    elif args.scenario == "ask":
        scenario_text_consult_holiday(sess)
    elif args.scenario == "follow_up":
        scenario_draft_follow_up(sess)
    elif args.scenario == "all":
        scenario_photo_write_post(sess)
        scenario_photo_knowledge(sess)
        scenario_photo_consult(sess)
        scenario_text_asset_decision(sess)
        scenario_text_consult_holiday(sess)
        scenario_draft_follow_up(sess)

    print(_c(_DIM, "\n\n沙盒模擬完畢。"))


if __name__ == "__main__":
    main()
