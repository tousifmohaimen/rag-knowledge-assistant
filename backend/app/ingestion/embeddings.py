"""Turn text into vectors.

Two implementations behind one interface:

* ``OpenAIEmbedder`` calls the OpenAI embeddings API in batches, retrying on rate
  limits and server errors.
* ``HashingEmbedder`` needs no network or key. It hashes each word into one of the
  vector's slots (the "hashing trick"), so texts that share words get similar vectors.
  That makes it useful for local development and tests, but it doesn't understand
  meaning: "car" and "automobile" look unrelated to it.
"""

import asyncio
import hashlib
import logging
import math
import random
import re
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.db.models import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+")
_RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})


class EmbeddingError(Exception):
    pass


class Embedder(Protocol):
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


class HashingEmbedder:
    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in _WORD.findall(text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            slot = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[slot] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # pgvector can't compute cosine distance for a zero vector.
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]

    async def aclose(self) -> None:
        return None


class OpenAIEmbedder:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        batch_size: int = 64,
        dimensions: int = EMBEDDING_DIMENSIONS,
        max_attempts: int = 5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.dimensions = dimensions
        self._model = model
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(await self._embed_batch(texts[start : start + self._batch_size]))
        return vectors

    async def _embed_batch(self, batch: Sequence[str]) -> list[list[float]]:
        payload = {
            "model": self._model,
            "input": list(batch),
            "dimensions": self.dimensions,
        }
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post("/embeddings", json=payload)
            except httpx.TransportError as exc:
                if attempt == self._max_attempts:
                    raise EmbeddingError(f"embedding request failed: {exc}") from exc
                await asyncio.sleep(_backoff(attempt))
                continue

            if response.status_code in _RETRY_STATUSES and attempt < self._max_attempts:
                delay = _retry_after(response) or _backoff(attempt)
                logger.warning(
                    "embedding API returned %s, retrying in %.1fs", response.status_code, delay
                )
                await asyncio.sleep(delay)
                continue
            if response.is_error:
                # Don't include the response body: it can echo back the input text.
                raise EmbeddingError(f"embedding API returned HTTP {response.status_code}")
            return self._parse(response.json(), expected=len(batch))
        raise EmbeddingError("embedding API kept failing")  # pragma: no cover

    def _parse(self, body: dict[str, Any], *, expected: int) -> list[list[float]]:
        items = sorted(body.get("data", []), key=lambda item: item["index"])
        if len(items) != expected:
            raise EmbeddingError(f"expected {expected} embeddings, got {len(items)}")
        vectors: list[list[float]] = [item["embedding"] for item in items]
        if any(len(v) != self.dimensions for v in vectors):
            raise EmbeddingError(
                f"embedding size doesn't match the database column ({self.dimensions})"
            )
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()


def _backoff(attempt: int) -> float:
    # Exponential backoff with jitter, so many workers don't retry in lockstep.
    return float(min(30.0, 2 ** (attempt - 1)) * (0.5 + random.random()))


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return min(60.0, max(0.0, float(value)))
    except ValueError:
        return None


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "openai":
        assert settings.openai_api_key is not None  # checked when settings load
        return OpenAIEmbedder(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.embedding_model,
            base_url=settings.openai_base_url,
            batch_size=settings.embedding_batch_size,
        )
    return HashingEmbedder()
