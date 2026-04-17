"""Dependency injection module.

Multi-feed (D-05): a single node runs N OdvServices (one per feed) under a
shared oracle policy. HTTP endpoints dispatch by `feed_id` path parameter.
"""

from typing import Dict

from fastapi import HTTPException

from node.core.errors import NodeNotInitializedError
from node.core.odv import OdvService

_services: Dict[str, OdvService] = {}


def register_odv_service(services: Dict[str, OdvService]) -> None:
    """Replace the registered per-feed service map (called at startup)."""
    global _services
    _services = dict(services)


async def get_odv_service_by_feed(feed_id: str) -> OdvService:
    """Fetch the OdvService for a given feed_id, or 404."""
    if not _services:
        raise NodeNotInitializedError()
    service = _services.get(feed_id)
    if service is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown feed_id '{feed_id}'. "
            f"Registered feeds: {sorted(_services.keys())}",
        )
    return service


async def get_any_odv_service() -> OdvService:
    """Return any registered service — used when the dispatch key is not a
    feed_id (e.g. legacy /sign by tx_body without feed routing).

    Prefer `get_odv_service_by_feed` when a feed_id is available.
    """
    if not _services:
        raise NodeNotInitializedError()
    return next(iter(_services.values()))
