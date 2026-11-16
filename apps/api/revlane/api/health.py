"""Health route: tells the console whether the API and its database are up."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from revlane import __version__
from revlane.config import Settings, get_settings
from revlane.db.session import get_engine

router = APIRouter()

DatabaseStatus = Literal["ok", "unavailable"]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    database: DatabaseStatus
    llm_mode: Literal["stub", "live"]


def database_status() -> DatabaseStatus:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return "unavailable"
    return "ok"


@router.get("/health")
def health(
    database: Annotated[DatabaseStatus, Depends(database_status)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Health:
    return Health(
        status="ok" if database == "ok" else "degraded",
        service="revlane-api",
        version=__version__,
        database=database,
        llm_mode=settings.llm_mode,
    )
