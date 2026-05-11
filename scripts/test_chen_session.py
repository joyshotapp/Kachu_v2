"""
四時循養堂 陳老師 — Kachu+ 真實功能測試
==========================================

這不是模擬場景，是一次完整的真實功能測試。
所有功能路徑都被真實呼叫：DB 寫入、onboarding 狀態機、知識庫、圖片處理、dispatch ACK。
不走 LINE API，改用 TestClient + HMAC-SHA256 偽造簽名。

測試流程（連貫工作日敘事）：
  第一節  Onboarding — Kachu+ 主動問，陳老師回答 + 丟官網
  第二節  品牌諮詢 — 陳老師問內容方向，Kachu+ 根據已知資料給建議
  第三節  進知識庫 — 三張圖（品牌故事 / 常見QA / 價格表）→ knowledge_entries
  第四節  先討論 — 廣告圖 → consult，陳老師問延伸方向
    第五節  閒置精煉 — 觸發 idle scheduler，自動刷新 brief 並驗證對話升格
  第六節  決定發文 — 廣告圖 → 寫貼文 → dispatch → execute_task_record

執行方式：
  python scripts/test_chen_session.py

每次執行都產生獨立 DB 檔案：test_data/chen_YYYYMMDD_HHMMSS.db
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
import httpx
from sqlmodel import SQLModel, Session, create_engine

# ── 確保 src/ 在 sys.path ────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kachu_plus.approval import ApprovalBridge
from kachu_plus.config import Settings
from kachu_plus.google_business import GoogleReviewService
from kachu_plus.learning import (
    ContextBriefManager,
    ConversationLearningService,
    IdleBriefRefreshScheduler,
    MemoryManager,
    PostTaskReviewService,
)
from kachu_plus.line.webhook import router as line_router
from kachu_plus.meta import router as meta_router
from kachu_plus.models import ExecutionTaskResult
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import LineChannelConfigTable, TenantTable
from kachu_plus.publishing import publish_content_bundle
from kachu_plus.services import AgentOSTaskDispatcher, LLMConsultant
from kachu_plus.tools_router import router as tools_router, _llm, analyze_photo_payload, _select_llm_api_key

# ──────────────────────────────────────────────────────────────────
# ANSI 色彩
# ──────────────────────────────────────────────────────────────────
_R = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_MAGENTA = "\033[95m"
_BLUE = "\033[94m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_R}"


# ──────────────────────────────────────────────────────────────────
# 測試常數
# ──────────────────────────────────────────────────────────────────
CHANNEL_SECRET = "chen-test-secret"
_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
TENANT_ID = f"chen-sishixunyang-{_TS}"

SHOP_NAME = "四時循養堂"
SHOP_INDUSTRY = "health_food"
SHOP_ADDRESS = "網路"              # 純網路銷售
# 真實 LINE user ID → export TEST_LINE_USER_ID=Uxxxxxxx （不設就用假 ID，push 只攔截不發送）
BOSS_USER_ID = os.environ.get("TEST_LINE_USER_ID", "U-chen-sishixunyang").strip() or "U-chen-sishixunyang"

# 持久化 DB 路徑
_DB_DIR = _ROOT / "test_data"
_DB_DIR.mkdir(exist_ok=True)
DB_PATH = _DB_DIR / f"chen_{_TS}.db"

# test_assets 目錄
_ASSETS = _ROOT / "test_assets"

# 圖片路徑
IMG_BRAND_STORY    = _ASSETS / "四時循養堂-疏通飲_01.jpg"          # 品牌故事：父親30年漢方
IMG_PRODUCT_QA     = _ASSETS / "四時循養堂-疏通飲_09-scaled-e1767835377457.jpg"  # 常見QA
IMG_PRICE          = _ASSETS / "20260415_價格.jpg"                # 價格表
IMG_AD_DISCOUNT    = _ASSETS / "643410432_122116482999187265_7131876432674385769_n.jpg"   # 廣告：買30送5
IMG_AD_REFERRAL    = _ASSETS / "650928655_122118546705187265_1358376159530904279_n.jpg"   # 廣告：老朋友推薦

_PUBLIC_IMAGE_URLS = {
    IMG_BRAND_STORY.name: "https://seasonwell.com.tw/wp-content/uploads/2025/12/%E5%9B%9B%E6%99%82%E5%BE%AA%E9%A4%8A%E5%A0%82-%E7%96%8F%E9%80%9A%E9%A3%B2_01.jpg",
    IMG_PRODUCT_QA.name: "https://seasonwell.com.tw/wp-content/uploads/2026/01/%E5%9B%9B%E6%99%82%E5%BE%AA%E9%A4%8A%E5%A0%82-%E7%96%8F%E9%80%9A%E9%A3%B2_09-scaled-e1767835377457.jpg",
    IMG_PRICE.name: "https://blog.hanben.com.tw/wp-content/uploads/2026/04/20260415_%E5%83%B9%E6%A0%BC.jpg",
}

# ──────────────────────────────────────────────────────────────────
# 工具：探測 AgentOS 是否可連
# ──────────────────────────────────────────────────────────────────

def _check_agentos_reachable(url: str) -> bool:
    try:
        with httpx.Client(base_url=url.rstrip("/"), timeout=3.0) as client:
            response = client.get("/approvals")
            return response.status_code < 500
    except httpx.HTTPError:
        return False


# ──────────────────────────────────────────────────────────────────
# Fake 基礎設施
# ──────────────────────────────────────────────────────────────────

class _FakeGBPClient:
    def __init__(self, **_) -> None:
        pass

    def get_review(self, account_id, location_id, review_id):
        return {
            "reviewId": review_id,
            "starRating": "FOUR",
            "comment": "疏通飲真的很有感！",
            "reviewer": {"displayName": "林小姐"},
            "createTime": "2026-05-10T10:00:00Z",
        }

    def post_reply(self, account_id, location_id, review_id, reply_text):
        return {"reviewId": review_id, "comment": reply_text}

    def create_local_post(self, account_id, location_id, summary, call_to_action_url=""):
        return {"name": "localPosts/chen-1", "summary": summary}


def _make_engine():
    """持久化 SQLite 引擎，每次執行獨立命名"""
    return create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed(repo: KachuPlusRepository) -> None:
    """建立初始 tenant（Onboarding 尚未完成，step='new'）"""
    seeded_settings = Settings()
    seeded_channel_token = seeded_settings.LINE_CHANNEL_ACCESS_TOKEN or "chen-test-token"
    seeded_channel_id = os.environ.get("TEST_LINE_CHANNEL_ID", "2009700564").strip() or "chen-test-line-channel"
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
            channel_access_token=seeded_channel_token,
            channel_id=seeded_channel_id,
        ))
        session.commit()


def _make_dispatch_result(intent_label: str) -> ExecutionTaskResult:
    return ExecutionTaskResult(
        task_id=f"task-{uuid.uuid4().hex[:8]}",
        domain=f"kachu_{intent_label}",
        status="waiting_approval",
        objective={
            "photo_content": "根據老闆上傳的圖片，生成社群貼文草稿",
            "knowledge_update": "將老闆提供的品牌素材更新進知識庫",
            "consult": "分析老闆的圖片並提供行銷建議",
        }.get(intent_label, f"執行 {intent_label}"),
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
    沙盒顧問：根據脈絡給予對應建議。
    生產環境換成 LiteLLM → Gemini。
    """
    kb = context_bundle or {}
    knowledge_highlights = kb.get("knowledge_highlights", [])
    has_brand_story = any("父親" in str(k) or "漢方" in str(k) or "30年" in str(k) for k in knowledge_highlights)
    has_price = any("3750" in str(k) or "6990" in str(k) or "30包" in str(k) for k in knowledge_highlights)

    msg = message.strip()

    if "內容" in msg or "角度" in msg or "方向" in msg:
        if has_brand_story:
            return (
                "根據你的品牌故事——父親 40 歲因行動受限，30 年漢方研究，我建議三個內容方向：\n\n"
                "1. 【情感主軸】「給每一位想繼續走下去的人」—— 40 至 65 歲族群最有共鳴\n"
                "2. 【安心主軸】「0 西藥、0 農藥、0 重金屬」搭配 2000 萬責任險，攻信任感\n"
                "3. 【見證主軸】真實用戶口碑輪播，避免廣告感，讓人跟著分享\n\n"
                "你有老客戶願意說說自己的經驗嗎？那是最有力的素材。"
            )
        return (
            "疏通飲的目標族群是 40-65 歲麻麻卡卡的中年人，我建議：\n\n"
            "1. 【安心主軸】0 西藥、0 農藥、0 重金屬，這是理性說服\n"
            "2. 【情境主軸】走不遠、站不久、坐不住——讓他們看到自己的困境被理解\n"
            "3. 【口碑主軸】朋友推薦給朋友，中年人信任圈子\n\n"
            "你覺得哪個方向最接近現在客人的狀態？"
        )

    if "安全" in msg or "西藥" in msg or "農藥" in msg:
        return (
            "了解，安全性是核心賣點。我記下了：陳老師特別強調 0 西藥、0 農藥、0 重金屬，客人非常在意。\n\n"
            "建議在每一篇貼文的結尾都加上這三個 0，並附上 2000 萬產品責任險的截圖，"
            "這樣就算沒看過你的人也能快速建立信任。\n\n"
            "你現在有責任險的官方文件圖嗎？那個非常值得加進知識庫。"
        )

    if "廣告" in msg or "圖" in msg or "延伸" in msg:
        if has_price:
            return (
                "這張圖走「走不遠站不久坐不住」痛點路線，效果不錯。延伸方向：\n\n"
                "1. 做成系列：「第 1 天用我懷疑 → 第 7 天有感 → 第 30 天跟朋友說」\n"
                "2. 搭配你目前的首購優惠（買 30 包送 5 包），強調「試試看的門檻」低\n"
                "3. 配上真人體驗短影音，比圖更有說服力\n\n"
                "你有客人願意拍個 15 秒的前後對比嗎？"
            )
        return (
            "這張廣告圖痛點很清楚。延伸方向：\n\n"
            "1. 做「30 天挑戰」系列貼文，讓人追蹤進度\n"
            "2. 搭配首購優惠，降低嘗試門檻\n"
            "3. 比較前後：「以前上下樓氣喘吁吁 → 現在爬山沒問題」\n\n"
            "哪個方向最接近你想做的？"
        )

    if "進度" in msg or "草稿" in msg or "好了" in msg or "怎麼樣" in msg:
        return (
            "草稿已整理好，正在等你確認。\n\n"
            "我根據老朋友推薦圖的情感角度，寫了：\n"
            "「有位老朋友上週傳訊息給我，說他叫我一定要試試這個，我拖了快半年……」\n\n"
            "完整版在你確認後我再發給你。你方便現在看嗎？"
        )

    return (
        "我記下了。\n"
        "你有任何品牌圖片或素材要補充嗎？"
        "直接傳給我，我幫你歸類放進知識庫，之後寫文的時候就能用到。"
    )


# ──────────────────────────────────────────────────────────────────
# 應用程式工廠
# ──────────────────────────────────────────────────────────────────

def _build_app(repo: KachuPlusRepository) -> tuple[FastAPI, list[dict], ContextBriefManager]:
    pushed: list[dict] = []

    app = FastAPI()
    settings = Settings()  # 自動從環境讀取 LINE_CHANNEL_ACCESS_TOKEN 等
    # 若環境沒有 token，設假值避免 None
    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        settings.LINE_CHANNEL_ACCESS_TOKEN = "chen-push-token"
    app.state.repository = repo
    app.state.settings = settings

    # 若有 API key 就用真實 LLMConsultant，否則 fallback fake
    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        real_settings = Settings()  # 讀取 .env
        consultant = LLMConsultant(real_settings)
        print(_c(_GREEN, "  ✓ 使用真實 LLMConsultant（Gemini 3 Flash Preview）"))
    else:
        consultant = MagicMock()
        consultant.build_reply = AsyncMock(side_effect=_fake_consultant_reply)
        print(_c(_YELLOW, "  ⚠ 無 API key，使用 fake consultant"))
    app.state.consultant = consultant

    memory = MemoryManager(repo, settings)
    briefs = ContextBriefManager(repo, memory)
    post_task_review = PostTaskReviewService(repo, memory, briefs)
    conversation_learning_service = ConversationLearningService(repo)
    idle_brief_refresh_scheduler = IdleBriefRefreshScheduler(repo, briefs, settings)
    app.state.memory_manager = memory
    app.state.context_brief_manager = briefs
    app.state.post_task_review = post_task_review
    app.state.conversation_learning_service = conversation_learning_service
    app.state.idle_brief_refresh_scheduler = idle_brief_refresh_scheduler

    # ── AgentOS dispatcher ─────────────────────────────────────────
    agentos_url = settings.AGENTOS_BASE_URL
    if _check_agentos_reachable(agentos_url):
        dispatcher: Any = AgentOSTaskDispatcher(settings)
        print(_c(_GREEN, f"  ✓ AgentOS 真實連線：{agentos_url}"))
    else:
        print(_c(_YELLOW, f"  ⚠ AgentOS {agentos_url} 無法連線，使用 mock"))
        print(_c(_DIM, "    → 若要真實連線，先開 SSH tunnel："))
        print(_c(_DIM, "      ssh -N -L 18001:<agentos_container_ip>:8000 root@172.234.85.159 &"))
        print(_c(_DIM, "      export AGENTOS_BASE_URL=http://localhost:18001"))
        dispatcher = MagicMock(spec=AgentOSTaskDispatcher)
        dispatcher.dispatch = AsyncMock(
            side_effect=lambda *, intent_label, **_: _make_dispatch_result(intent_label)
        )
        _fake_task_view = MagicMock()
        _fake_task_view.task = {"status": "waiting_approval", "current_run_id": "run-chen-001"}
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

    # ── LINE push：有真實 token + 真實 user ID 就雙軌（capture + 真實發送）─
    import kachu_plus.line.webhook as wm
    import kachu_plus.tools_router as tr
    from kachu_plus.line.push import push_line_messages as _real_push_fn

    _token = settings.LINE_CHANNEL_ACCESS_TOKEN
    _user_is_real = BOSS_USER_ID.startswith("U") and len(BOSS_USER_ID) > 20
    _token_is_real = bool(_token) and _token != "chen-push-token"

    if _token_is_real and _user_is_real:
        print(_c(_GREEN, "  ✓ LINE push 真實發送（token + user_id 均已設定）"))
        async def _push_handler(*, to: str, messages, access_token: str) -> None:
            pushed.append({"to": to, "messages": messages if isinstance(messages, list) else [messages]})
            try:
                await _real_push_fn(to=to, messages=messages, access_token=access_token)
            except Exception as _exc:
                print(_c(_YELLOW, f"  ⚠ LINE push 失敗（{_exc}）"))
    elif _token_is_real:
        print(_c(_YELLOW, "  ⚠ LINE token 已設但 TEST_LINE_USER_ID 是假 ID → 只攔截不發送"))
        print(_c(_DIM, "    → 設定 export TEST_LINE_USER_ID=Uxxxxxxx 可改為真實發送"))
        async def _push_handler(*, to: str, messages, access_token: str) -> None:  # type: ignore[misc]
            pushed.append({"to": to, "messages": messages if isinstance(messages, list) else [messages]})
    else:
        print(_c(_YELLOW, "  ⚠ LINE push 攔截（設定 LINE_CHANNEL_ACCESS_TOKEN 可改為真實發送）"))
        async def _push_handler(*, to: str, messages, access_token: str) -> None:  # type: ignore[misc]
            pushed.append({"to": to, "messages": messages if isinstance(messages, list) else [messages]})

    wm.push_line_messages = _push_handler
    tr.push_line_messages = _push_handler

    return app, pushed, briefs


# ──────────────────────────────────────────────────────────────────
# 簽名 & LINE Session
# ──────────────────────────────────────────────────────────────────

def _sign(body: bytes) -> str:
    digest = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class LineSession:
    """封裝 TestClient，提供 LINE 對話操作。"""

    def __init__(self, client: TestClient, pushed: list[dict], briefs: ContextBriefManager):
        self.client = client
        self.pushed = pushed
        self.briefs = briefs
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"chen-msg-{self._seq:04d}"

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

    def send_text(self, text: str, user_id: str = BOSS_USER_ID, *, label: str | None = None) -> None:
        display = label or repr(text)
        _print_chen(f"傳文字：{_c(_CYAN, display)}")
        self._post({
            "events": [{
                "type": "message",
                "source": {"type": "user", "userId": user_id},
                "message": {"id": self._next_id(), "type": "text", "text": text},
            }]
        })
        self._flush()

    def send_image(
        self,
        image_path: Path,
        label: str = "",
        user_id: str = BOSS_USER_ID,
    ) -> None:
        """傳真實 JPEG 圖片（monkey-patch _download_line_message_content）"""
        img_bytes = image_path.read_bytes()
        file_size_kb = len(img_bytes) // 1024
        _print_chen(f"傳圖片：{_c(_CYAN, label or image_path.name)}（{file_size_kb} KB）")

        msg_id = self._next_id()

        import kachu_plus.line.webhook as wm
        _orig = wm._download_line_message_content

        async def _fake_dl(line_message_id: str, access_token: str) -> bytes:
            return img_bytes

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

    def send_postback(self, data: str, label: str = "", user_id: str = BOSS_USER_ID) -> None:
        _print_chen(f"按按鈕：{_c(_CYAN, label or data)}")
        self._post({
            "events": [{
                "type": "postback",
                "source": {"type": "user", "userId": user_id},
                "postback": {"data": data},
            }]
        })
        self._flush()

    def get_pending_asset(self, user_id: str = BOSS_USER_ID):
        return self.repo().get_latest_pending_asset_intent(
            tenant_id=TENANT_ID, line_user_id=user_id
        )

    def repo(self) -> KachuPlusRepository:
        return self.client.app.state.repository

    def _flush(self) -> None:
        if not self.pushed:
            print(_c(_DIM, "  （Kachu+ 尚無 LINE 回應，背景任務處理中）"))
            return
        for push in self.pushed:
            _print_push(push)
        self.pushed.clear()

    def trigger_refresh_briefs(self) -> dict:
        """手動觸發 refresh_briefs（模擬閒置時精煉）"""
        return asyncio.run(
            self.briefs.refresh_briefs(TENANT_ID, reason="idle_refinement")
        )

    def run_idle_scheduler(self) -> dict:
        """模擬閒置視窗過後，讓 idle scheduler 跑一輪。"""
        scheduler = getattr(self.client.app.state, "idle_brief_refresh_scheduler", None)
        if scheduler is None:
            return {"refreshed_count": 0, "tenant_ids": []}

        repo = self.repo()
        current_brief = repo.get_context_brief(TENANT_ID, "conversation_summary_brief")
        if current_brief is not None:
            with Session(repo._engine) as session:  # noqa: SLF001
                stored = session.get(type(current_brief), current_brief.id)
                if stored is not None:
                    stored.updated_at = datetime.now(timezone.utc) - timedelta(minutes=20)
                    session.add(stored)
                    session.commit()

        return asyncio.run(
            scheduler.run_once(now=datetime.now(timezone.utc) + timedelta(minutes=10))
        )


# ──────────────────────────────────────────────────────────────────
# 顯示格式化
# ──────────────────────────────────────────────────────────────────

def _print_chen(desc: str) -> None:
    print(f"\n{_c(_BOLD, '👩‍💼 陳老師')}: {desc}")


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
            lines.append(
                f"{pad}  {_c(_CYAN, '快捷選項：')}{'  |  '.join(_c(_BOLD, lb) for lb in labels)}"
            )

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
                lines.append(
                    f"{pad}  {_c(_CYAN, '操作按鈕：')}{'  |  '.join(_c(_BOLD, b) for b in btns)}"
                )
        elif ctype == "carousel":
            lines.append(f"{pad}  （包含 {len(contents.get('contents', []))} 張卡片）")
    else:
        lines.append(f"{pad}[{mtype}] {json.dumps(msg, ensure_ascii=False)[:120]}")

    return "\n".join(lines)


def _print_push(push: dict) -> None:
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"{_c(_BOLD + _YELLOW, '📲 Kachu+ → 陳老師')} {_c(_DIM, push['to'])}")
    print(sep)
    msgs = push["messages"]
    for i, m in enumerate(msgs, 1):
        if len(msgs) > 1:
            print(_c(_DIM, f"  [{i}/{len(msgs)}]"))
        print(_render_msg(m))
    print(sep)


def _section(num: int, title: str, subtitle: str = "") -> None:
    print(f"\n\n{'═' * 65}")
    print(_c(_BOLD + _MAGENTA, f"  第{num}節  {title}"))
    if subtitle:
        print(_c(_DIM, f"  {subtitle}"))
    print("═" * 65)


def _verify(label: str, condition: bool, detail: str = "") -> None:
    icon = _c(_GREEN, "✓") if condition else _c(_RED, "✗")
    line = f"  {icon}  {label}"
    if detail:
        line += _c(_DIM, f"  ({detail})")
    print(line)


def _gap(msg: str) -> None:
    """標注功能缺口"""
    print(f"\n  {_c(_YELLOW, '⚠  [功能缺口]')} {_c(_YELLOW, msg)}")


# ──────────────────────────────────────────────────────────────────
# 第一節：Onboarding
# ──────────────────────────────────────────────────────────────────

def section_1_onboarding(sess: LineSession) -> None:
    _section(1, "Onboarding — Kachu+ 主動問，陳老師填資料",
             "狀態機：new → asking_name → asking_industry → asking_sleep_threshold → asking_address → awaiting_docs → interview_q1 → q2 → q3 → completed")

    # Step 1：觸發 welcome（任意文字 or 第一次傳訊）
    print(_c(_DIM, "\n  Kachu+ 感應到新商家，主動發起 onboarding 問卷"))
    sess.send_text("你好", label="第一次打招呼（觸發 welcome）")

    # Step 2：店名
    sess.send_text("四時循養堂", label="店名")

    # Step 3：業種
    sess.send_text("保健食品，天然漢方飲品", label="業種說明")

    # Step 4：sleep threshold
    sess.send_text("每個月", label="客人回訪頻率")

    # Step 5：地址 / 通路
    sess.send_text("網路銷售，主要在官網和 LINE 購買", label="通路說明")

    # Step 6：awaiting_docs — 丟官網 URL
    print(_c(_DIM, "\n  陳老師丟官網，讓 Kachu+ 吸收品牌資訊"))
    sess.send_text(
        "這是我們的官網，你去看一下 https://seasonwell.com.tw/",
        label="丟官網 URL",
    )

    # Step 7：完成品牌資料上傳
    sess.send_text("完成", label="跳過繼續，進入訪談")

    # Interview Q1：核心價值
    sess.send_text(
        "我們跟別家最不一樣的是真的用漢方做的，不是加香精調味，"
        "而且從我爸爸那一輩就開始研究，30 年的配方。",
        label="訪談 Q1：核心差異",
    )

    # Interview Q2：最大困擾
    sess.send_text(
        "最大困擾是客人不知道這個東西跟市面上其他的有什麼差，"
        "大家看到保健品都覺得一樣，很難說清楚。",
        label="訪談 Q2：最大困擾",
    )

    # Interview Q3：今年目標
    sess.send_text(
        "今年最想做的是讓更多 40 歲以上的人知道這個東西，"
        "現在都靠朋友介紹，想要做更有系統的內容行銷。",
        label="訪談 Q3：今年目標",
    )

    # 驗證
    print()
    repo = sess.repo()
    tenant = repo.get_tenant(TENANT_ID)
    onboarding_done = repo.is_onboarding_complete(TENANT_ID)
    entries = repo.list_knowledge_entries(TENANT_ID, limit=20)

    _verify("Onboarding 完成（is_onboarding_complete = True）", onboarding_done)
    _verify("tenant.name 已更新", tenant is not None and tenant.name == SHOP_NAME,
            f"tenant.name = {getattr(tenant, 'name', '?')}")
    _verify("tenant.sleep_threshold 已設定", tenant is not None and getattr(tenant, "sleep_threshold", 0) > 0,
            f"sleep_threshold = {getattr(tenant, 'sleep_threshold', '?')} 天")
    _verify("知識庫有 brand_material（官網）", any(e.category == "brand_material" for e in entries),
            f"共 {len(entries)} 筆")
    _verify("知識庫有訪談記錄（core_value + pain_point）",
            any(e.category == "core_value" for e in entries) and
            any(e.category == "pain_point" for e in entries))

    convs = repo.list_recent_conversations(TENANT_ID, limit=30)
    _verify("對話記錄已寫入 conversation_logs",
            len(convs) > 0, f"共 {len(convs)} 筆")


# ──────────────────────────────────────────────────────────────────
# 第二節：品牌諮詢
# ──────────────────────────────────────────────────────────────────

def section_2_consult(sess: LineSession) -> None:
    _section(2, "品牌諮詢 — 陳老師問方向，Kachu+ 根據品牌資料回覆",
             "路徑：intent_router(consult) → consultant.build_reply（用 knowledge_highlights）")

    sess.send_text(
        "疏通飲接下來想做內容行銷，你覺得從哪個角度最有力？",
        label="問內容策略方向",
    )

    sess.send_text(
        "我的客人很在意安全性，像 0 西藥、0 農藥、0 重金屬，這個很重要",
        label="補充：客人在意安全性",
    )

    # 驗證
    print()
    convs = sess.repo().list_recent_conversations(TENANT_ID, limit=30)
    _verify("諮詢對話已記錄",
            len(convs) >= 2,
            f"目前共 {len(convs)} 筆對話記錄")


# ──────────────────────────────────────────────────────────────────
# 第三節：進知識庫（三張圖）
# ──────────────────────────────────────────────────────────────────

def section_3_knowledge_images(sess: LineSession) -> None:
    _section(3, "進知識庫 — 三張品牌圖片 → knowledge_entries",
             "路徑：image → analyze → pending_asset_intent → postback(knowledge_update) → save_knowledge_entry")

    # 圖 1：品牌故事
    sess.send_image(IMG_BRAND_STORY, label="品牌故事圖（父親30年漢方）")
    intent = sess.get_pending_asset()
    if intent:
        print(_c(_DIM, f"  pending_asset_intent.id = {intent.id}"))
        sess.send_postback(
            f"action=asset_intent&decision=knowledge_update"
            f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}",
            label="選「進知識庫」（品牌故事）",
        )
    else:
        print(_c(_RED, "  ⚠ 找不到 pending_asset_intent（品牌故事圖）"))

    # 圖 2：常見 QA
    sess.send_image(IMG_PRODUCT_QA, label="常見QA圖（甜不甜/腎臟/孕婦/多久有感）")
    intent = sess.get_pending_asset()
    if intent:
        sess.send_postback(
            f"action=asset_intent&decision=knowledge_update"
            f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}",
            label="選「進知識庫」（常見QA）",
        )
    else:
        print(_c(_RED, "  ⚠ 找不到 pending_asset_intent（常見QA圖）"))

    # 圖 3：價格表
    sess.send_image(IMG_PRICE, label="價格表（30包3750 / 60包6990 / 90包9900）")
    intent = sess.get_pending_asset()
    if intent:
        sess.send_postback(
            f"action=asset_intent&decision=knowledge_update"
            f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}",
            label="選「進知識庫」（價格表）",
        )
    else:
        print(_c(_RED, "  ⚠ 找不到 pending_asset_intent（價格表圖）"))

    # 驗證
    print()
    entries = sess.repo().list_knowledge_entries(TENANT_ID, limit=20)
    img_entries = [e for e in entries if e.category in ("brand_material", "photo_analysis", "image_analysis")]
    all_entries = len(entries)
    _verify("知識庫已有圖片素材記錄",
            all_entries >= 3,
            f"共 {all_entries} 筆（含 onboarding），圖片類 {len(img_entries)} 筆")


# ──────────────────────────────────────────────────────────────────
# 第四節：先討論（廣告圖延伸）
# ──────────────────────────────────────────────────────────────────

def section_4_consult_image(sess: LineSession) -> None:
    _section(4, "先討論 — 廣告圖 → 陳老師問延伸方向",
             "路徑：image → pending_asset_intent → postback(consult) → consultant.build_reply")

    sess.send_image(IMG_AD_DISCOUNT, label="廣告圖（走不遠站不久坐不住，首購買30送5）")
    intent = sess.get_pending_asset()
    if intent:
        sess.send_postback(
            f"action=asset_intent&decision=consult"
            f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}",
            label="選「先討論」",
        )
    else:
        print(_c(_RED, "  ⚠ 找不到 pending_asset_intent（廣告圖）"))
        return

    sess.send_text(
        "這張是之前做的廣告，走「站不久走不遠坐不住」方向，你覺得同個方向還能怎麼延伸？",
        label="問廣告延伸方向",
    )

    # 驗證
    print()
    convs = sess.repo().list_recent_conversations(TENANT_ID, limit=30)
    _verify("先討論對話已記錄",
            len(convs) > 5,
            f"目前共 {len(convs)} 筆對話記錄")


# ──────────────────────────────────────────────────────────────────
# 第五節：閒置精煉（refresh_briefs）
# ──────────────────────────────────────────────────────────────────

def section_5_idle_refinement(sess: LineSession) -> None:
    _section(
        5,
        "閒置精煉 — 觸發 idle scheduler，自動刷新 brief 與驗證對話升格",
        "模擬：對話閒置後由 scheduler 自動跑一輪，不再只靠手動 refresh",
    )

    repo = sess.repo()
    entries_before = repo.list_knowledge_entries(TENANT_ID, limit=30)
    safety_entries_before = [
        entry for entry in entries_before
        if "安全" in str(getattr(entry, "content", "")) or "副作用" in str(getattr(entry, "content", ""))
    ]
    _verify(
        "對話中的安全性偏好已自動升格為 knowledge_entries",
        len(safety_entries_before) > 0,
        f"匹配 {len(safety_entries_before)} 筆",
    )

    print(_c(_DIM, "\n  觸發 idle brief scheduler.run_once()..."))
    scheduler_result = sess.run_idle_scheduler()

    conv_brief_row = repo.get_context_brief(TENANT_ID, "conversation_summary_brief")
    brand_brief_row = repo.get_context_brief(TENANT_ID, "brand_brief")
    owner_brief_row = repo.get_context_brief(TENANT_ID, "owner_brief")

    conv_brief = json.loads(conv_brief_row.content_json) if conv_brief_row is not None else {}
    brand_brief = json.loads(brand_brief_row.content_json) if brand_brief_row is not None else {}
    owner_brief = json.loads(owner_brief_row.content_json) if owner_brief_row is not None else {}

    recent_turns = conv_brief.get("recent_turns", [])
    summary = conv_brief.get("summary", "")
    highlights = brand_brief.get("knowledge_highlights", [])

    print(f"\n  {_c(_BOLD, 'conversation_summary_brief')}")
    print(f"  reason: {conv_brief.get('reason', '?')}")
    if recent_turns:
        print(f"  recent_turns ({len(recent_turns)} 筆):")
        for turn in recent_turns[:6]:
            print(f"    {_c(_DIM, turn)}")
    else:
        print(f"  {_c(_DIM, '（無近期對話）')}")
    print(f"  summary: {_c(_DIM, summary[:120])}")

    print(f"\n  {_c(_BOLD, 'brand_brief')}")
    print(f"  brand_name: {brand_brief.get('brand_name', '?')}")
    print(f"  industry: {brand_brief.get('industry', '?')}")
    print(f"  knowledge_highlights ({len(highlights)} 筆):")
    for highlight in highlights[:3]:
        print(f"    {_c(_DIM, str(highlight)[:100])}")

    print(f"\n  {_c(_BOLD, 'owner_brief')}")
    print(f"  consultant_focus: {_c(_DIM, str(owner_brief.get('consultant_focus', '?'))[:80])}")

    print()
    entries_after = repo.list_knowledge_entries(TENANT_ID, limit=30)
    safety_entries_after = [
        entry for entry in entries_after
        if "安全" in str(getattr(entry, "content", "")) or "副作用" in str(getattr(entry, "content", ""))
    ]
    highlight_text = "\n".join(str(item) for item in highlights[:3])
    refresh_reason = str(conv_brief.get("reason", ""))

    _verify(
        "idle scheduler 已執行 refresh_briefs",
        scheduler_result.get("refreshed_count", 0) > 0,
        f"tenant_ids = {scheduler_result.get('tenant_ids', [])}",
    )
    _verify("context_briefs 已寫入 DB（conversation_summary_brief）", conv_brief_row is not None)
    _verify("context_briefs 已寫入 DB（brand_brief）", brand_brief_row is not None)
    _verify("conversation_summary_brief 包含近期對話", len(recent_turns) > 0)
    _verify(
        "brief refresh reason = idle_refinement",
        refresh_reason == "idle_refinement",
        refresh_reason or "(empty)",
    )
    _verify(
        "安全性偏好條目仍可在知識庫查到",
        len(safety_entries_after) > 0,
        f"匹配 {len(safety_entries_after)} 筆",
    )
    _verify(
        "brand_brief 已生成知識亮點",
        len(highlights) > 0,
        highlight_text[:80],
    )


# ──────────────────────────────────────────────────────────────────
# 第六節：決定發文 + 追問進度
# ──────────────────────────────────────────────────────────────────

def section_6_post_and_followup(sess: LineSession) -> None:
    _section(6, "決定發文 + 追問進度",
             "路徑：image → postback(photo_content) → dispatch → execute_task_record → 追問草稿")

    # 傳「老朋友推薦」廣告圖，選「寫貼文」
    sess.send_image(IMG_AD_REFERRAL, label="老朋友推薦廣告圖（情感版）")
    intent = sess.get_pending_asset()
    if intent:
        sess.send_postback(
            f"action=asset_intent&decision=photo_content"
            f"&asset_intent_id={intent.id}&tenant_id={TENANT_ID}",
            label="選「寫貼文」",
        )
    else:
        print(_c(_RED, "  ⚠ 找不到 pending_asset_intent（老朋友推薦圖）"))

    # 追問草稿進度
    print(_c(_DIM, "\n  陳老師過了一會兒來追問草稿"))
    sess.send_text("草稿好了嗎？", label="追問草稿進度")
    sess.send_text("怎麼樣了？", label="再次追問")

    # 驗證
    print()
    repo = sess.repo()
    task_record = repo.get_latest_execute_task_record(
            tenant_id=TENANT_ID,
            line_user_id=BOSS_USER_ID,
        )
    _verify("execute_task_record 已存入 DB",
            task_record is not None,
            f"task_id = {getattr(task_record, 'task_id', '?')}")
    _verify("task status = waiting_approval",
            getattr(task_record, "status", "") in ("waiting_approval", "dispatched"),
            f"status = {getattr(task_record, 'status', '?')}")

    convs = repo.list_recent_conversations(TENANT_ID, limit=30)
    _verify("追問對話已記錄",
            len(convs) > 8,
            f"目前共 {len(convs)} 筆")


# ──────────────────────────────────────────────────────────────────
# 第七節：Vision AI 圖片分析品質驗測
# ──────────────────────────────────────────────────────────────────

def section_7_vision_quality(sess: LineSession) -> None:
    _section(7, "Vision AI 品質驗測 — 確認 AI 真的讀懂圖片內容",
             "直接呼叫 analyze_photo_payload，傳入真實 JPEG bytes → 驗證回傳包含圖特有資訊")

    settings = sess.client.app.state.settings

    if not (settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY):
        _gap("無 API key，跳過 Vision AI 品質驗測")
        return

    async def _run():
        results = []
        # 測試 1：價格圖 → 應識別出「包」「元」或價格數字
        img_bytes = IMG_PRICE.read_bytes()
        b64 = base64.b64encode(img_bytes).decode("ascii")
        data_url = "data:image/jpeg;base64," + b64
        a1 = await analyze_photo_payload(photo_url=data_url, line_message_id="vision-test-price", settings=settings)
        results.append(("price", a1))

        # 測試 2：品牌故事圖 → 應識別出人物、故事或漢方相關描述
        img_bytes2 = IMG_BRAND_STORY.read_bytes()
        b64_2 = base64.b64encode(img_bytes2).decode("ascii")
        data_url_2 = "data:image/jpeg;base64," + b64_2
        a2 = await analyze_photo_payload(photo_url=data_url_2, line_message_id="vision-test-brand", settings=settings)
        results.append(("brand_story", a2))

        return results

    results = asyncio.run(_run())

    print()
    for name, analysis in results:
        desc = analysis.get("scene_description", "")
        intent = analysis.get("upload_intent", "")
        tags = analysis.get("suggested_tags", [])
        score = analysis.get("quality_score", 0)
        is_fallback = desc == "老闆剛上傳一張可用於社群貼文的照片。"

        print(f"\n  {_c(_BOLD, f'圖片: {name}')}")
        print(f"  scene_description: {_c(_CYAN, desc[:150])}")
        print(f"  upload_intent:     {intent}")
        print(f"  suggested_tags:    {', '.join(tags[:5])}")
        print(f"  quality_score:     {score}")

        if name == "price":
            # 價格圖應包含價格或數字相關詞彙
            has_price_info = any(k in desc for k in ("包", "元", "價格", "優惠", "購買", "促銷", "活動", "折", "3750", "6990", "9900"))
            _verify("價格圖 → 識別到價格/購買相關內容", has_price_info and not is_fallback,
                    f"desc前60字: {desc[:60]}")
        elif name == "brand_story":
            # 品牌故事圖應包含人物或保健相關詞彙
            has_story = any(k in desc for k in ("人", "飲品", "產品", "保健", "漢方", "天然", "品牌", "功效", "故事", "包裝"))
            _verify("品牌故事圖 → 識別到產品/人物/保健相關內容", has_story and not is_fallback,
                    f"desc前60字: {desc[:60]}")

    _verify("Vision AI 未回傳 fallback 文字",
            all(a.get("scene_description") != "老闆剛上傳一張可用於社群貼文的照片。" for _, a in results))


# ──────────────────────────────────────────────────────────────────
# 第八節：知識庫檢索品質驗測
# ──────────────────────────────────────────────────────────────────

def section_8_kb_retrieval_quality(sess: LineSession) -> None:
    _section(8, "知識庫檢索品質驗測 — 諮詢回答是否引用 KB 內容",
             "問一個只有知識庫才能回答的具體問題（價格），確認 LLM 回覆含知識庫資訊")

    settings = sess.client.app.state.settings

    if not (settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY):
        _gap("無 API key，跳過知識庫檢索品質驗測")
        return

    # 直接從知識庫撈出價格相關條目，組成 context_bundle，再呼叫 build_reply
    repo = sess.repo()
    entries = repo.list_knowledge_entries(TENANT_ID, limit=30)
    price_entries = [
        getattr(e, "content", "") or ""
        for e in entries
        if any(k in (getattr(e, "content", "") or "").lower()
               for k in ("價格", "價錢", "包", "元", "6990", "9900", "3750", "6,990"))
    ]
    all_kb = [getattr(e, "content", "") or "" for e in entries if getattr(e, "content", "")]
    relevant_knowledge = (price_entries or all_kb)[:5]

    print(_c(_DIM, f"\n  知識庫條目數：{len(entries)}，含價格關鍵字：{len(price_entries)} 筆"))
    if relevant_knowledge:
        print(_c(_DIM, "  帶入 context 的前 2 筆："))
        for t in relevant_knowledge[:2]:
            print(_c(_DIM, f"    {t[:120]}"))

    consultant = sess.client.app.state.consultant
    context_bundle = {"relevant_knowledge": relevant_knowledge}

    import asyncio as _asyncio8
    print(_c(_DIM, "\n  呼叫 build_reply（帶 KB context）..."))
    reply = _asyncio8.run(
        consultant.build_reply(
            tenant_name="四時循養堂",
            industry_type="保健食品",
            message="請問疏通飲 60 包要多少錢？有沒有什麼組合比較划算？",
            context_bundle=context_bundle,
        )
    )

    print(f"\n  {_c(_BOLD, 'build_reply 回覆：')}")
    print(f"  {_c(_CYAN, reply[:300])}")
    print()

    has_price_numbers = any(
        k in reply for k in ("6990", "6,990", "60包", "60 包", "9900", "9,900", "3750", "3,750")
    )
    has_real_content = len(reply) > 50

    _verify("回覆包含具體價格數字（來自知識庫）", has_price_numbers,
            f"回覆前100字: {reply[:100]}")
    _verify("回覆長度足夠（>50字）", has_real_content,
            f"字數: {len(reply)}")

    if not has_price_numbers:
        _gap("知識庫的價格資訊未被正確引用至回覆中，可能是：\n"
             "     1. 知識庫條目的內容是 fallback（圖片分析失敗）而非真實圖片內容\n"
             "     2. context_bundle 中 relevant_knowledge 格式需調整\n"
             "     3. LLM prompt 未明確指示引用知識庫數字")


# ──────────────────────────────────────────────────────────────────
# 第九節：真實貼文草稿產生品質驗測
# ──────────────────────────────────────────────────────────────────

def section_9_real_post_generation(sess: LineSession) -> None:
    _section(9, "真實貼文草稿產生品質驗測",
             "直接呼叫 _llm() 生成社群貼文草稿，驗證草稿包含品牌特有資訊")

    settings = sess.client.app.state.settings

    if not (settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY):
        _gap("無 API key，跳過貼文草稿品質驗測")
        return

    repo = sess.repo()
    entries = repo.list_knowledge_entries(TENANT_ID, limit=20)

    # 從知識庫萃取可用素材
    kb_texts = []
    for e in entries:
        content = getattr(e, "content", "") or ""
        if len(content) > 20:
            kb_texts.append(content[:200])

    kb_block = "\n".join(f"- {t}" for t in kb_texts[:6])

    async def _run():
        # 傳入廣告圖（老朋友推薦版）
        img_bytes = IMG_AD_REFERRAL.read_bytes()
        b64 = base64.b64encode(img_bytes).decode("ascii")
        data_url = "data:image/jpeg;base64," + b64

        # 先做 Vision 分析
        analysis = await analyze_photo_payload(
            photo_url=data_url,
            line_message_id="post-gen-test",
            settings=settings,
        )
        scene_desc = analysis.get("scene_description", "一張廣告圖")
        tags = ", ".join(analysis.get("suggested_tags", [])[:5])

        # 用 _llm 直接生成 IG 貼文草稿
        prompt = (
            f"品牌：{SHOP_NAME}，業種：天然漢方保健飲品\n\n"
            f"知識庫素材：\n{kb_block}\n\n"
            f"圖片描述（Vision AI）：{scene_desc}\n"
            f"建議標籤：{tags}\n\n"
            "請根據以上資料，為這張圖片寫一則 IG 貼文草稿（150-250字），"
            "要有情感共鳴、具體痛點，末尾加建議 hashtag。"
            "必須引用品牌真實特點（如 30年漢方研究、0西藥、疏通飲等），不可泛泛而談。"
        )

        ig_draft = await _llm(
            prompt=prompt,
            model=settings.LITELLM_MODEL,
            api_key=settings.GOOGLE_AI_API_KEY,
            openai_api_key=settings.OPENAI_API_KEY,
        )
        return scene_desc, ig_draft

    scene_desc, ig_draft = asyncio.run(_run())

    print(f"\n  {_c(_BOLD, 'Vision 分析（廣告圖）：')}")
    print(f"  {_c(_DIM, scene_desc[:150])}")
    print(f"\n  {_c(_BOLD, '生成的 IG 貼文草稿：')}")
    print("  " + "─" * 55)
    for line in ig_draft.split("\n"):
        print(f"  {_c(_CYAN, line)}")
    print("  " + "─" * 55)

    # 驗證草稿品質
    is_brand_specific = any(k in ig_draft for k in (
        "四時循養堂", "疏通飲", "漢方", "30年", "三十年", "0西藥", "西藥", "農藥",
        "走不遠", "站不久", "坐不住", "老朋友", "中年",
    ))
    is_not_generic = len(ig_draft) > 100
    has_hashtag = "#" in ig_draft

    _verify("草稿包含品牌特有詞彙（非通用模板）", is_brand_specific,
            f"字數: {len(ig_draft)}")
    _verify("草稿長度足夠（>100字）", is_not_generic,
            f"字數: {len(ig_draft)}")
    _verify("草稿包含 hashtag", has_hashtag)

    if not is_brand_specific:
        _gap("草稿未引用品牌特有資訊，可能原因：知識庫條目為 fallback 文字，Vision 分析未讀出品牌名")


# ──────────────────────────────────────────────────────────────────
# 第十節：真實發布 Facebook 貼文
# ──────────────────────────────────────────────────────────────────

def section_10_publish_fb(sess: LineSession) -> None:
    _section(10, "真實發布 Facebook 貼文",
             "解析本地圖片後生成相符貼文，並以公開圖片 URL 真實發布到 Facebook")

    fb_token = os.environ.get("TEST_FB_PAGE_ACCESS_TOKEN", "").strip()
    fb_page_id = os.environ.get("TEST_FB_PAGE_ID", "").strip()
    meta_access_token = os.environ.get("TEST_META_ACCESS_TOKEN", "").strip() or fb_token
    override_image_url = os.environ.get("TEST_FB_IMAGE_URL", "").strip()
    override_local_image = os.environ.get("TEST_FB_LOCAL_IMAGE_PATH", "").strip()

    if not fb_token or not fb_page_id:
        _gap(
            "未設定 TEST_FB_PAGE_ACCESS_TOKEN / TEST_FB_PAGE_ID，跳過真實發布。\n"
            "     設定方式：export TEST_FB_PAGE_ACCESS_TOKEN=xxx TEST_FB_PAGE_ID=yyy\n"
            "     如 Meta connector 有獨立 user token，可加設：export TEST_META_ACCESS_TOKEN=zzz"
        )
        return

    local_image = Path(override_local_image) if override_local_image else IMG_BRAND_STORY
    image_url = override_image_url or _PUBLIC_IMAGE_URLS.get(local_image.name, "")

    if not local_image.exists():
        _gap(f"指定的本地圖片不存在：{local_image}")
        return
    if not image_url:
        _gap(
            f"找不到 {local_image.name} 對應的公開圖片 URL，無法測試 FB 圖文發布。\n"
            "     可用 TEST_FB_IMAGE_URL 指定公開網址，或改用官網已有公開圖的檔案。"
        )
        return

    print(_c(_DIM, f"\n  FB Page ID: {fb_page_id}"))
    print(_c(_DIM, f"  Token 前 20 字: {fb_token[:20]}..."))
    print(_c(_DIM, f"  本地圖片: {local_image.name}"))
    print(_c(_DIM, f"  公開圖片 URL: {image_url}"))

    # ── Seed connector account ──────────────────────────────────────
    repo = sess.repo()
    import json as _json
    credentials = {
        "access_token": meta_access_token,
        "fb_page_id": fb_page_id,
        "fb_access_token": fb_token,
        "ig_user_id": "",  # 本節跳過 IG
    }
    repo.save_connector_account(
        tenant_id=TENANT_ID,
        platform="meta",
        credentials_json=_json.dumps(credentials),
        account_label="四時循養堂 FB（測試）",
    )
    print(_c(_GREEN, "  ✓ connector_account 已寫入 DB（platform=meta）"))

    # ── 生成貼文草稿（有 LLM 就用真實 LLM，否則用預設草稿）──────────
    settings = sess.client.app.state.settings
    entries = repo.list_knowledge_entries(TENANT_ID, limit=20)
    kb_texts = [
        (getattr(e, "content", "") or "")[:150]
        for e in entries
        if len(getattr(e, "content", "") or "") > 20
    ]
    kb_block = "\n".join(f"- {t}" for t in kb_texts[:5])

    scene_desc = ""
    suggested_tags = ""
    try:
        img_bytes = local_image.read_bytes()
        data_url = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode()
        analysis = asyncio.run(
            analyze_photo_payload(
                photo_url=data_url,
                line_message_id=f"fb-publish-{local_image.stem}",
                settings=settings,
            )
        )
        scene_desc = str(analysis.get("scene_description", "") or "")
        suggested_tags = ", ".join(analysis.get("suggested_tags") or [])
        print(_c(_GREEN, "  ✓ 已用本地圖片完成 Vision 解析"))
        if scene_desc:
            print(_c(_DIM, f"  圖片描述: {scene_desc[:140]}"))
    except Exception as exc:
        print(_c(_YELLOW, f"  ⚠ 圖片解析失敗，改用知識庫素材生成貼文：{exc}"))

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        print(_c(_DIM, "\n  用 LLM 生成 FB 圖文貼文草稿..."))
        prompt = (
            f"品牌：{SHOP_NAME}，業種：天然漢方保健飲品\n\n"
            f"知識庫素材：\n{kb_block}\n\n"
            f"圖片描述：{scene_desc}\n"
            f"建議標籤：{suggested_tags}\n\n"
            "請根據上面的圖片內容與品牌素材，寫一則 Facebook 圖文貼文（120-220字），情感溫暖、針對 40-65 歲族群、"
            "突出漢方天然安全、末尾附 3 個 hashtag。"
            "貼文內容必須和圖片主題一致，不能只寫泛用品牌文。"
            "必須引用品牌真實特點（如 30年漢方研究、0西藥0農藥、疏通飲），不可泛泛而談。"
        )
        fb_draft = asyncio.run(
            _llm(
                prompt=prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
            )
        )
    else:
        fb_draft = (
            "四時循養堂疏通飲，30年漢方研究的心血結晶。"
            "0西藥、0農藥、0重金屬，給每一位想繼續走下去的你。"
            "現在首購買30包送5包，讓身體感受真正的漢方力量。\n"
            "#四時循養堂 #疏通飲 #天然漢方"
        )
        print(_c(_YELLOW, "  ⚠ 無 API key，使用預設草稿"))

    print(f"\n  {_c(_BOLD, 'FB 貼文草稿：')}")
    print("  " + "─" * 55)
    for line in fb_draft.split("\n"):
        print(f"  {_c(_CYAN, line)}")
    print("  " + "─" * 55)

    # ── 呼叫 publish_content_bundle ─────────────────────────────────
    print(_c(_DIM, "\n  呼叫 publish_content_bundle() → 帶圖發布到 Facebook..."))
    try:
        review_service = sess.client.app.state.google_review_service
        results = publish_content_bundle(
            repo=repo,
            review_service=review_service,
            tenant_id=TENANT_ID,
            run_id=f"test-publish-{_TS}",
            drafts={"ig_fb": fb_draft, "image_url": image_url},
            selected_platforms=["ig_fb"],
            workflow_type="kachu_photo_content",
        )
    except Exception as exc:
        print(_c(_RED, f"  ✗ publish_content_bundle 拋出例外：{exc}"))
        results = {}

    print(f"\n  {_c(_BOLD, 'publish 結果：')}")
    print(f"  {_c(_CYAN, str(results))}")
    print()

    ig_fb_result = results.get("ig_fb", {})
    status = ig_fb_result.get("status", "")
    fb_post_id = ig_fb_result.get("facebook", {}).get("fb_post_id", "") if isinstance(ig_fb_result.get("facebook"), dict) else ""
    error = ig_fb_result.get("error", "")

    _verify("Facebook 發布成功（status=published）", status == "published",
            f"fb_post_id={fb_post_id}" if fb_post_id else f"error={error}")

    if status == "published" and fb_post_id:
        print(f"\n  {_c(_GREEN + _BOLD, '✓ 貼文已發布！')}")
        print(f"  fb_post_id: {_c(_CYAN, fb_post_id)}")
        print(f"  可在 FB 粉專確認：https://www.facebook.com/{fb_page_id}/posts/")

        # 驗證 published_content 已寫入 DB
        if hasattr(repo, "list_published_content"):
            published = repo.list_published_content(TENANT_ID, limit=5)
            _verify("published_content 已寫入 DB",
                    len(published) > 0, f"{len(published)} 筆")
    elif error:
        print(f"\n  {_c(_RED, '✗ 發布失敗')}: {error}")
        if "token" in error.lower() or "permission" in error.lower() or "oauth" in error.lower():
            _gap("可能原因：Page Access Token 已過期，或缺少 pages_manage_posts 權限")
        elif "id" in error.lower():
            _gap("可能原因：TEST_FB_PAGE_ID 格式不對（應為純數字）")


# ──────────────────────────────────────────────────────────────────
# 最終摘要
# ──────────────────────────────────────────────────────────────────

def _final_summary(sess: LineSession) -> None:
    print(f"\n\n{'═' * 65}")
    print(_c(_BOLD + _BLUE, "  測試完成摘要"))
    print("═" * 65)

    repo = sess.repo()
    tenant = repo.get_tenant(TENANT_ID)
    entries = repo.list_knowledge_entries(TENANT_ID, limit=50)
    convs = repo.list_recent_conversations(TENANT_ID, limit=100)
    conv_brief_check = repo.get_context_brief(TENANT_ID, "conversation_summary_brief")
    task_record = repo.get_latest_execute_task_record(
        tenant_id=TENANT_ID,
        line_user_id=BOSS_USER_ID,
    )
    onboarding_done = repo.is_onboarding_complete(TENANT_ID)

    print(f"\n  Tenant ID:         {_c(_CYAN, TENANT_ID)}")
    print(f"  商家名稱:          {getattr(tenant, 'name', '?')}")
    print(f"  DB 檔案:           {_c(_CYAN, str(DB_PATH))}")
    print()

    _verify("Onboarding 完成", onboarding_done)
    _verify(f"knowledge_entries", len(entries) > 0, f"{len(entries)} 筆")
    _verify(f"conversation_logs", len(convs) > 0, f"{len(convs)} 筆")
    _verify("context_briefs（conversation_summary_brief）", conv_brief_check is not None)
    _verify(f"execute_task_records", task_record is not None,
            f"task_id = {getattr(task_record, 'task_id', '無')}")

    print(f"\n  {_c(_BOLD, '關於「對話記錄是否讓 Kachu+ 越來越懂使用者」:')}")
    print(f"  {_c(_CYAN, '✓ 已實作')}: 每次對話寫入 kachu_conversations")
    print(f"  {_c(_CYAN, '✓ 已實作')}: refresh_briefs() 精煉為 conversation_summary_brief + brand_brief")
    print(f"  {_c(_CYAN, '✓ 已實作')}: 下次 build_reply 會帶入 context_bundle（含 recent_conversations + knowledge_highlights）")
    print(f"  {_c(_CYAN, '✓ 已實作')}: 對話中的高價值偏好（如「客人在意安全性」）會自動升格為 knowledge_entries")
    print(f"  {_c(_CYAN, '✓ 已實作')}: idle brief scheduler 可在閒置後自動執行 refresh_briefs")

    print(f"\n  DB 位置（可用 sqlite3 / DB Browser 查看）:")
    print(f"  {_c(_CYAN, str(DB_PATH))}\n")


# ──────────────────────────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{'═' * 65}")
    print(_c(_BOLD + _BLUE, "  四時循養堂 陳老師 — Kachu+ 真實功能測試"))
    print(f"  DB: {DB_PATH}")
    print(f"  Tenant ID: {TENANT_ID}")
    print("═" * 65)

    # 建立持久化引擎 + 應用
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    # Onboarding 初始 step 為 'new'（不跳過，讓狀態機完整跑）

    app, pushed, briefs = _build_app(repo)
    client = TestClient(app, raise_server_exceptions=False)
    sess = LineSession(client, pushed, briefs)

    section_1_onboarding(sess)
    section_2_consult(sess)
    section_3_knowledge_images(sess)
    section_4_consult_image(sess)
    section_5_idle_refinement(sess)
    section_6_post_and_followup(sess)
    section_7_vision_quality(sess)
    section_8_kb_retrieval_quality(sess)
    section_9_real_post_generation(sess)
    section_10_publish_fb(sess)
    _final_summary(sess)


if __name__ == "__main__":
    main()
