"""
GET /api/deployment/{deployment_id}/stream
Server-Sent Events для real-time логов деплоя.

Типы событий:
- connected  : подключение установлено {"deployment_id": "..."}
- log        : строка лога {"level": "INFO|WARN|ERROR|CMD", "message": "..."}
- phase      : смена фазы  {"phase": "cluster_config", "progress": 45}
- completed  : успешное завершение {}
- failed     : ошибка {"error": "..."}
"""
import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# deployment_id → asyncio.Queue[dict]
_event_queues: dict[str, asyncio.Queue] = {}


def get_or_create_queue(deployment_id: str) -> asyncio.Queue:
    if deployment_id not in _event_queues:
        _event_queues[deployment_id] = asyncio.Queue(maxsize=1000)
    return _event_queues[deployment_id]


async def push_event(deployment_id: str, event_type: str, data: dict) -> None:
    """
    Вызывается из orchestrator'а для отправки событий клиенту.
    Не бросает исключений — при переполнении очереди событие дропается.
    """
    queue = get_or_create_queue(deployment_id)
    try:
        queue.put_nowait({"type": event_type, "data": data})
    except asyncio.QueueFull:
        logger.warning("SSE queue full for %s, dropping %s event", deployment_id, event_type)


def remove_queue(deployment_id: str) -> None:
    _event_queues.pop(deployment_id, None)


async def _sse_generator(deployment_id: str) -> AsyncGenerator[str, None]:
    queue = get_or_create_queue(deployment_id)
    try:
        # Первое событие — подтверждение подключения
        yield f"data: {json.dumps({'type': 'connected', 'deployment_id': deployment_id})}\n\n"

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("completed", "failed"):
                    break
            except asyncio.TimeoutError:
                # keepalive — не даёт nginx/браузеру закрыть соединение
                yield ": keepalive\n\n"
    finally:
        remove_queue(deployment_id)


@router.get(
    "/{deployment_id}/stream",
    summary="SSE: real-time события деплоя",
    response_class=StreamingResponse,
)
async def stream_deployment_events(deployment_id: str) -> StreamingResponse:
    """
    Server-Sent Events endpoint для подписки на события конкретного деплоя.

    Использование на клиенте:
        const es = new EventSource(`/api/deployment/${id}/stream`);
        es.onmessage = e => { const ev = JSON.parse(e.data); ... };
    """
    return StreamingResponse(
        _sse_generator(deployment_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # отключает буферизацию nginx
            "Connection": "keep-alive",
        },
    )
