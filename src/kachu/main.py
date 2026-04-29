from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse

from .agentOS_client import AgentOSClient
from .approval_bridge import ApprovalBridge
from .auth import oauth_router
from .config import Settings, get_settings
from .google.webhook import router as google_webhook_router
from .intent_router import IntentRouter
from .line import line_webhook_router
from .memory import MemoryManager
from .onboarding import OnboardingFlow
from .persistence import KachuRepository, create_db_engine, init_db
from .policy import KachuExecutionPolicyResolver
from .scheduler import KachuScheduler
from .tools import tools_router
from .dashboard import dashboard_router


def create_app(settings: Settings | None = None, _engine=None) -> FastAPI:
    if settings is None:
        settings = get_settings()
    settings.validate_production_config()
    if settings.APP_ENV == "production" and not settings.ALLOW_SCHEMA_CREATE_IN_PRODUCTION:
        raise RuntimeError(
            "Automatic schema creation is disabled in production; run migrations before startup."
        )

    # Database
    if _engine is not None:
        engine = _engine
    else:
        engine = create_db_engine(settings.DATABASE_URL)
    init_db(engine)

    # Services
    repository = KachuRepository(engine)
    agentOS_client = AgentOSClient(settings)
    approval_bridge = ApprovalBridge(agentOS_client, repository, settings)
    memory_manager = MemoryManager(repository, settings)
    # Phase 4: adaptive execution policy
    policy_resolver = KachuExecutionPolicyResolver(repository)
    intent_router = IntentRouter(agentOS_client, repository, settings, policy_resolver)
    onboarding_flow = OnboardingFlow(repository, settings, intent_router)
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

    return app
