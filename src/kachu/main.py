from __future__ import annotations

import json
import logging
import pathlib
from contextlib import asynccontextmanager
from html import escape

from fastapi import FastAPI, HTTPException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
from fastapi.responses import FileResponse, HTMLResponse

from .agentOS_client import AgentOSClient
from .approval_bridge import ApprovalBridge
from .business_consultant import BusinessConsultant
from .context_brief_manager import ContextBriefManager
from .auth import oauth_router
from .config import Settings, get_settings
from .google.webhook import router as google_webhook_router
from .intent_router import IntentRouter
from .line.webhook import router as line_webhook_router
from .memory import MemoryManager
from .merchant_pages import load_merchant_page_payload
from .onboarding import OnboardingFlow
from .persistence import KachuRepository, assert_schema_migrated, create_db_engine, init_db
from .policy import KachuExecutionPolicyResolver
from .post_task_review import PostTaskReviewService
from .scheduler import KachuScheduler
from .tools import tools_router
from .dashboard import dashboard_router

def _render_merchant_page(payload: dict[str, object]) -> HTMLResponse:
        merchant_name = escape(str(payload.get("merchant_name", "")))
        category = escape(str(payload.get("category", "")))
        tagline = escape(str(payload.get("tagline", "")))
        phone = escape(str(payload.get("phone", "")))
        line_id = escape(str(payload.get("line_id", "")))
        booking_note = escape(str(payload.get("booking_note", "")))
        address = escape(str(payload.get("address", "")))
        business_hours = escape(str(payload.get("business_hours", "")))
        service_mode = escape(str(payload.get("service_mode", "")))
        canonical_url = escape(str(payload.get("canonical_url", "")))
        brand_intro = [escape(str(item)) for item in payload.get("brand_intro", [])]
        service_tags = [escape(str(item)) for item in payload.get("service_tags", [])]
        service_scenarios = [escape(str(item)) for item in payload.get("service_scenarios", [])]
        featured_points = [escape(str(item)) for item in payload.get("featured_points", [])]
        services = payload.get("services", [])
        line_url = escape(str(payload.get("line_url", "https://line.me/R/ti/p/@067ggwva")))

        service_tag_html = "".join(
                f'<span class="rounded-full bg-emerald-50 px-4 py-2 text-emerald-800">{tag}</span>' for tag in service_tags
        )
        featured_point_html = "".join(f"<li>{point}</li>" for point in featured_points)
        brand_intro_html = "".join(f"<p>{paragraph}</p>" for paragraph in brand_intro)
        service_card_html = "".join(
                (
                        '<div class="rounded-2xl border border-amber-100 bg-amber-50/60 p-5">'
                        f'<h3 class="font-bold text-slate-900">{escape(str(item.get("title", "")))}</h3>'
                        f'<p class="mt-2 text-sm leading-7 text-slate-600">{escape(str(item.get("description", "")))}</p>'
                        "</div>"
                )
                for item in services
        )
        service_scenario_html = "".join(
                f'<div class="rounded-2xl border border-slate-200 p-4">{scenario}</div>' for scenario in service_scenarios
        )

        html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>示範商家頁｜{merchant_name}｜Kachu</title>
    <meta name="description" content="Kachu 示範 tenant 商家頁，展示 {merchant_name} 的基本資料、服務內容、聯絡方式與正式商家頁樣式。" />
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@500;700;900&family=Noto+Sans+TC:wght@400;500;700&display=swap');
        html {{ font-family: 'Noto Sans TC', sans-serif; }}
        .headline {{ font-family: 'Noto Serif TC', serif; }}
        .page-bg {{
            background:
                radial-gradient(circle at top left, rgba(217, 119, 6, 0.12), transparent 32%),
                radial-gradient(circle at top right, rgba(16, 185, 129, 0.10), transparent 28%),
                linear-gradient(180deg, #fffaf2 0%, #ffffff 42%);
        }}
    </style>
</head>
<body class="page-bg text-slate-800 antialiased">
    <main class="max-w-5xl mx-auto px-6 py-10 sm:py-14">
        <div class="flex flex-wrap items-center justify-between gap-4">
            <a href="/" class="inline-flex items-center gap-2 text-sm font-semibold text-amber-800 hover:text-amber-900">
                <span>←</span>
                <span>返回 Kachu 首頁</span>
            </a>
            <span class="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-4 py-1.5 text-xs font-semibold tracking-[0.18em] text-amber-800 uppercase">
                Kachu Merchant Demo
            </span>
        </div>

        <section class="mt-8 overflow-hidden rounded-[2rem] border border-amber-100 bg-white/90 shadow-[0_24px_80px_rgba(120,53,15,0.10)]">
            <div class="grid gap-0 lg:grid-cols-[1.2fr_0.8fr]">
                <div class="p-8 sm:p-10 lg:p-12">
                    <p class="text-sm font-semibold tracking-[0.2em] text-emerald-700 uppercase">{category}</p>
                    <h1 class="headline mt-4 text-4xl font-black leading-tight text-slate-900 sm:text-5xl">{merchant_name}</h1>
                    <p class="mt-3 text-lg text-slate-500">{tagline}</p>

                    <div class="mt-8 grid gap-4 sm:grid-cols-2">
                        <div class="rounded-2xl border border-slate-200 bg-slate-50 p-5">
                            <div class="text-xs font-semibold tracking-[0.16em] text-slate-500 uppercase">聯絡方式</div>
                            <div class="mt-3 space-y-2 text-sm leading-7 text-slate-700">
                                <p>電話：{phone}</p>
                                <p>LINE：{line_id}</p>
                                <p>預約：{booking_note}</p>
                            </div>
                        </div>
                        <div class="rounded-2xl border border-slate-200 bg-slate-50 p-5">
                            <div class="text-xs font-semibold tracking-[0.16em] text-slate-500 uppercase">門市資訊</div>
                            <div class="mt-3 space-y-2 text-sm leading-7 text-slate-700">
                                <p>地址：{address}</p>
                                <p>營業時間：{business_hours}</p>
                                <p>服務型態：{service_mode}</p>
                            </div>
                        </div>
                    </div>

                    <div class="mt-8 flex flex-wrap gap-3 text-sm font-medium text-slate-700">{service_tag_html}</div>
                </div>

                <div class="bg-[linear-gradient(160deg,#0f766e_0%,#14532d_100%)] p-8 text-white sm:p-10">
                    <div class="rounded-3xl border border-white/20 bg-white/10 p-6 backdrop-blur">
                        <div class="text-xs font-semibold tracking-[0.18em] uppercase text-emerald-100">示範商家頁重點</div>
                        <ul class="mt-4 space-y-3 text-sm leading-7 text-emerald-50">{featured_point_html}</ul>
                    </div>
                </div>
            </div>
        </section>

        <section class="mt-8 grid gap-6 lg:grid-cols-[0.95fr_1.05fr]">
            <article class="rounded-[1.75rem] border border-slate-200 bg-white p-8 shadow-sm">
                <h2 class="headline text-2xl font-bold text-slate-900">品牌介紹</h2>
                <div class="mt-4 space-y-4 text-sm leading-8 text-slate-600">{brand_intro_html}</div>
                <div class="mt-5 rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm leading-7 text-slate-700">
                    <div class="text-xs font-semibold tracking-[0.16em] text-slate-500 uppercase">正式商家頁網址</div>
                    <div class="mt-2 break-all">{canonical_url}</div>
                </div>
            </article>

            <article class="rounded-[1.75rem] border border-slate-200 bg-white p-8 shadow-sm">
                <h2 class="headline text-2xl font-bold text-slate-900">主要服務</h2>
                <div class="mt-5 grid gap-4 sm:grid-cols-2">{service_card_html}</div>
            </article>
        </section>

        <section class="mt-8 rounded-[1.75rem] border border-slate-200 bg-white p-8 shadow-sm">
            <div class="flex flex-wrap items-start justify-between gap-5">
                <div>
                    <h2 class="headline text-2xl font-bold text-slate-900">常見預約情境</h2>
                    <p class="mt-2 text-sm leading-7 text-slate-600">適合近期有這些狀況的人先預約諮詢：</p>
                </div>
                <a href="{line_url}" class="inline-flex items-center rounded-xl bg-emerald-600 px-5 py-3 text-sm font-semibold text-white hover:bg-emerald-700">
                    立即用 LINE 預約
                </a>
            </div>
            <div class="mt-5 grid gap-3 sm:grid-cols-3 text-sm leading-7 text-slate-700">{service_scenario_html}</div>
        </section>
    </main>
</body>
</html>
"""
        return HTMLResponse(content=html)


def create_app(settings: Settings | None = None, _engine=None) -> FastAPI:
    if settings is None:
        settings = get_settings()
    settings.validate_production_config()

    # Database
    if _engine is not None:
        engine = _engine
    else:
        engine = create_db_engine(settings.DATABASE_URL)
    create_schema = settings.APP_ENV != "production" or settings.ALLOW_SCHEMA_CREATE_IN_PRODUCTION
    if settings.APP_ENV == "production" and not create_schema:
        assert_schema_migrated(engine)
    init_db(engine, create_schema=create_schema)

    # Services
    repository = KachuRepository(engine)
    agentOS_client = AgentOSClient(settings)
    memory_manager = MemoryManager(repository, settings)
    context_brief_manager = ContextBriefManager(repository, memory_manager)
    post_task_review = PostTaskReviewService(repository, memory_manager, context_brief_manager)
    approval_bridge = ApprovalBridge(
        agentOS_client,
        repository,
        settings,
        post_task_review=post_task_review,
    )
    business_consultant = BusinessConsultant(repository, memory_manager, settings)
    # Phase 4: adaptive execution policy
    policy_resolver = KachuExecutionPolicyResolver(repository)
    intent_router = IntentRouter(agentOS_client, repository, settings, policy_resolver)
    onboarding_flow = OnboardingFlow(
        repository,
        settings,
        intent_router,
        memory_manager=memory_manager,
        context_brief_manager=context_brief_manager,
        post_task_review=post_task_review,
    )
    # Phase 5: pass memory to scheduler for content calendar
    scheduler = KachuScheduler(agentOS_client, repository, settings, memory_manager, policy_resolver)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler.start()
        yield
        scheduler.shutdown()
        await agentOS_client.aclose()

    app = FastAPI(
        title="Kachu",
        version="0.1.0",
        description="Agent-native AI 虛擬小幕僚",
        lifespan=lifespan,
    )

    # App state
    app.state.settings = settings
    app.state.repository = repository
    app.state.agentOS_client = agentOS_client
    app.state.approval_bridge = approval_bridge
    app.state.intent_router = intent_router
    app.state.onboarding_flow = onboarding_flow
    app.state.memory_manager = memory_manager
    app.state.context_brief_manager = context_brief_manager
    app.state.post_task_review = post_task_review
    app.state.business_consultant = business_consultant
    app.state.policy_resolver = policy_resolver

    # Routers
    app.include_router(line_webhook_router)
    app.include_router(tools_router)
    app.include_router(oauth_router)
    app.include_router(dashboard_router)
    app.include_router(google_webhook_router)

    @app.get("/health")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok", "service": "kachu"}

    _static_dir = pathlib.Path(__file__).parent / "static"

    @app.get("/", include_in_schema=False)
    def landing_page() -> FileResponse:
        return FileResponse(_static_dir / "index.html", media_type="text/html")

    @app.get("/privacy", include_in_schema=False)
    @app.get("/privacy-policy", include_in_schema=False)
    def privacy_page() -> FileResponse:
        return FileResponse(_static_dir / "privacy.html", media_type="text/html")

    @app.get("/terms", include_in_schema=False)
    @app.get("/terms-of-service", include_in_schema=False)
    def terms_page() -> FileResponse:
        return FileResponse(_static_dir / "terms.html", media_type="text/html")

    @app.get("/merchants/{merchant_slug}", include_in_schema=False)
    def merchant_page(merchant_slug: str) -> HTMLResponse:
        try:
            payload = load_merchant_page_payload(_static_dir / "merchant_pages", merchant_slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Merchant page not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return _render_merchant_page(payload)

    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt() -> FileResponse:
        return FileResponse(_static_dir / "robots.txt", media_type="text/plain")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap_xml() -> FileResponse:
        return FileResponse(_static_dir / "sitemap.xml", media_type="application/xml")

    @app.get("/googledc0423ede5af4719.html", include_in_schema=False)
    def gsc_verify():
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("google-site-verification: googledc0423ede5af4719.html")

    return app
