"""The Revlane API application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from revlane import __version__
from revlane.api import (
    agents,
    auth,
    chat,
    connectors,
    console,
    errors,
    fee_contracts,
    findings,
    fixtures,
    health,
    lifecycle,
    limits,
    metrics,
)
from revlane.bootstrap import ensure_demo_users, ensure_tenant
from revlane.config import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Make sure the demo tenant and its people exist, so there is an account to
    sign in with. Skipped quietly if the database is not there yet."""
    settings = get_settings()
    try:
        ensure_tenant(settings.demo_tenant_id, settings.demo_tenant_name)
        ensure_demo_users(settings.demo_tenant_id, settings)
    except SQLAlchemyError:
        pass
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Revlane API", version=__version__, lifespan=lifespan)

    # Added first so that CORS wraps it: a refused request still carries CORS headers.
    limits.install(app, get_settings())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().web_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    errors.install(app)

    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(health.router)
    v1.include_router(auth.router)
    v1.include_router(fixtures.router)
    v1.include_router(connectors.router)
    v1.include_router(fee_contracts.router)
    v1.include_router(findings.router)
    v1.include_router(agents.router)
    v1.include_router(chat.router)
    v1.include_router(console.router)
    v1.include_router(lifecycle.router)
    v1.include_router(metrics.router)
    app.include_router(v1)
    return app


app = create_app()
