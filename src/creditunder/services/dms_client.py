from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger(__name__)


@dataclass
class DMSDocument:
    document_id: str
    document_name: str
    document_type: str
    content: bytes
    content_type: str


class DMSClient:
    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    async def fetch_document(self, document_id: str) -> DMSDocument:
        log.info("dms.fetch", document_id=document_id)
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self._base_url}/documents/{document_id}",
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            import base64
            content = base64.b64decode(data["content_base64"])
            return DMSDocument(
                document_id=document_id,
                document_name=data["document_name"],
                document_type=data["document_type"],
                content=content,
                content_type=data.get("content_type", "text/plain"),
            )

    async def upload_document(
        self,
        content: bytes,
        document_name: str,
        document_type: str,
        content_type: str = "application/pdf",
        related_application_id: str | None = None,
    ) -> str:
        import base64
        payload = {
            "document_name": document_name,
            "document_type": document_type,
            "content_base64": base64.b64encode(content).decode(),
            "content_type": content_type,
        }
        if related_application_id:
            payload["related_application_id"] = related_application_id

        log.info("dms.upload", document_name=document_name)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/documents",
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()["document_id"]
