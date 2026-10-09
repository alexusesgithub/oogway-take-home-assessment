"""Optional semantic embeddings via Ollama (`/api/embed`).

Disabled by default: on a CPU-only laptop nomic-embed-text runs at ~5
chunks/s, so embedding the full ~18k-chunk corpus takes about an hour. Lexical
search works immediately; when enabled, embeddings are back-filled in the
background and retrieval upgrades to hybrid automatically.
"""

from __future__ import annotations

import httpx

from app.config import Settings

# nomic-embed-text expects task prefixes for asymmetric retrieval.
QUERY_PREFIX = "search_query: "
DOC_PREFIX = "search_document: "


class OllamaEmbedder:
    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.embeddings_enabled
        self.model = settings.ollama_embed_model
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.timeout = httpx.Timeout(120.0, connect=5.0)

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": inputs})
            resp.raise_for_status()
            return resp.json()["embeddings"]

    async def embed_query(self, query: str) -> list[float]:
        return (await self._embed([QUERY_PREFIX + query]))[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed([DOC_PREFIX + t for t in texts])
