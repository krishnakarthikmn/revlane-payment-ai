"""Ask Revlane over HTTP: one question, answered as a stream of events."""

import json
from collections.abc import Iterator
from datetime import date

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from revlane.api.deps import ExistingTenant
from revlane.chat import service
from revlane.db.session import exact_json

router = APIRouter(prefix="/chat")


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    # Treat this date as today, for periods such as "last week". Default: today in India.
    as_of: date | None = None


def stream(events: Iterator[service.Event]) -> Iterator[str]:
    """Server-sent events: a name line and one line of JSON for each."""
    for item in events:
        data = json.dumps(json.loads(exact_json(item["data"])), ensure_ascii=False)
        yield f"event: {item['event']}\ndata: {data}\n\n"


@router.post("")
def ask(body: Question, tenant_id: ExistingTenant) -> StreamingResponse:
    """Streams `step`, `query`, `table`, `chart`, `answer` and `sources` events,
    or a `refusal`, and always ends with `done`."""
    events = service.ask(
        tenant_id, body.question.strip(), service.get_chat_model(), as_of=body.as_of
    )
    return StreamingResponse(
        stream(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
