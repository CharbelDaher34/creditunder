from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)


class EDWClient:
    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    async def export(self, payload: dict[str, Any]) -> str:
        """Submit final case payload to EDW. Returns EDW confirmation ID."""
        log.info("edw.export", application_id=payload.get("application_id"))
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/export",
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()["confirmation_id"]
