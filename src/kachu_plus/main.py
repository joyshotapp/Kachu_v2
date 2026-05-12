from __future__ import annotations

import logging

from fastapi import FastAPI
from sqlmodel import create_engine

from kachu_plus.approval import (
    ApprovalBridge,
    PendingApprovalSyncScheduler,
    PendingApprovalSyncService,
    ScheduledApprovalScheduler,
    ScheduledApprovalService,
)
from kachu_plus.config import get_settings
from kachu_plus.admin import router as admin_router
from kachu_plus.content_plans import ContentPlanScheduler, ContentPlanService
from kachu_plus.customer_tags import router as customer_tags_router
from kachu_plus.google_business import router as google_business_router
from kachu_plus.google_business import GoogleReviewService
from kachu_plus.meta import MetaInsightsService, router as meta_router
from kachu_plus.line.webhook import router as line_webhook_router
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.services import (
    AgentOSTaskDispatcher,
    ContextBriefManager,
    ConversationLearningService,
    IdleBriefRefreshScheduler,
    LLMConsultant,
    MemoryManager,
    PostTaskReviewService,
    ProactiveSuggestionEngine,
    KachuExecutionPolicyResolver,
    SleepCustomerQueryService,
    SleepProfileSyncService,
    SleepSyncScheduler,
)
from kachu_plus.suggestions import router as suggestions_router
from kachu_plus.tools_router import router as tools_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Kachu+", version="0.1.0")
    app.state.settings = settings
    app.state.execute_dispatcher = AgentOSTaskDispatcher(settings)
    app.state.consultant = LLMConsultant(settings)

    @app.on_event("startup")
    async def startup() -> None:
        engine = create_engine(settings.DATABASE_URL)
        repository = KachuPlusRepository(engine)
        app.state.repository = repository
        memory_manager = MemoryManager(repository, settings)
        context_brief_manager = ContextBriefManager(repository, memory_manager)
        conversation_learning_service = ConversationLearningService(repository)
        app.state.memory_manager = memory_manager
        app.state.context_brief_manager = context_brief_manager
        app.state.conversation_learning_service = conversation_learning_service
        app.state.post_task_review = PostTaskReviewService(repository, memory_manager, context_brief_manager)
        app.state.google_review_service = GoogleReviewService(repository, settings)
        app.state.approval_bridge = ApprovalBridge(app.state.execute_dispatcher, repository, app.state.post_task_review)
        app.state.policy_resolver = KachuExecutionPolicyResolver(repository)
        app.state.meta_insights_service = MetaInsightsService(repository, settings)
        content_plan_service = ContentPlanService(repository, settings, app.state.consultant)
        app.state.content_plan_service = content_plan_service
        content_plan_scheduler = ContentPlanScheduler(content_plan_service)
        app.state.content_plan_scheduler = content_plan_scheduler
        app.state.proactive_suggestion_engine = ProactiveSuggestionEngine(
            repository,
            settings,
            consultant=app.state.consultant,
            meta_service=app.state.meta_insights_service,
            dispatcher=app.state.execute_dispatcher,
        )
        from kachu_plus.proactive import ProactiveSuggestionScheduler

        proactive_scheduler = ProactiveSuggestionScheduler(app.state.proactive_suggestion_engine)
        app.state.proactive_suggestion_scheduler = proactive_scheduler
        scheduled_approval_service = ScheduledApprovalService(app.state.approval_bridge, repository)
        app.state.scheduled_approval_service = scheduled_approval_service
        scheduled_approval_scheduler = ScheduledApprovalScheduler(scheduled_approval_service)
        app.state.scheduled_approval_scheduler = scheduled_approval_scheduler
        approval_sync_service = PendingApprovalSyncService(app.state.execute_dispatcher, repository)
        app.state.pending_approval_sync_service = approval_sync_service
        pending_approval_sync_scheduler = PendingApprovalSyncScheduler(
            approval_sync_service,
            interval_seconds=settings.AGENTOS_APPROVAL_SYNC_INTERVAL_SECONDS,
        )
        app.state.pending_approval_sync_scheduler = pending_approval_sync_scheduler
        app.state.sleep_query_service = SleepCustomerQueryService(repository)
        sleep_profile_sync_service = SleepProfileSyncService(repository)
        app.state.sleep_profile_sync_service = sleep_profile_sync_service
        sleep_sync_scheduler = SleepSyncScheduler(sleep_profile_sync_service, settings)
        idle_brief_refresh_scheduler = IdleBriefRefreshScheduler(repository, context_brief_manager, settings)
        app.state.sleep_sync_scheduler = sleep_sync_scheduler
        app.state.idle_brief_refresh_scheduler = idle_brief_refresh_scheduler
        sleep_sync_scheduler.start()
        idle_brief_refresh_scheduler.start()
        content_plan_scheduler.start()
        proactive_scheduler.start()
        scheduled_approval_scheduler.start()
        if settings.AGENTOS_APPROVAL_SYNC_ENABLED:
            pending_approval_sync_scheduler.start()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        scheduler = getattr(app.state, "sleep_sync_scheduler", None)
        if scheduler is not None:
            await scheduler.shutdown()
        idle_brief_refresh_scheduler = getattr(app.state, "idle_brief_refresh_scheduler", None)
        if idle_brief_refresh_scheduler is not None:
            await idle_brief_refresh_scheduler.shutdown()
        content_plan_scheduler = getattr(app.state, "content_plan_scheduler", None)
        if content_plan_scheduler is not None:
            await content_plan_scheduler.shutdown()
        proactive_scheduler = getattr(app.state, "proactive_suggestion_scheduler", None)
        if proactive_scheduler is not None:
            await proactive_scheduler.shutdown()
        scheduled_approval_scheduler = getattr(app.state, "scheduled_approval_scheduler", None)
        if scheduled_approval_scheduler is not None:
            await scheduled_approval_scheduler.shutdown()
        pending_approval_sync_scheduler = getattr(app.state, "pending_approval_sync_scheduler", None)
        if pending_approval_sync_scheduler is not None:
            await pending_approval_sync_scheduler.shutdown()
        dispatcher = getattr(app.state, "execute_dispatcher", None)
        if dispatcher is not None:
            await dispatcher.aclose()

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    app.include_router(line_webhook_router)
    app.include_router(customer_tags_router)
    app.include_router(google_business_router)
    app.include_router(meta_router)
    app.include_router(tools_router)
    app.include_router(suggestions_router)
    app.include_router(admin_router)

    return app


app = create_app()
