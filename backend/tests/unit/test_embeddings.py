import json
import math

import httpx
import pytest

from app.ingestion.embeddings import EmbeddingError, HashingEmbedder, OpenAIEmbedder


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashingEmbedder(dimensions=64)

    first, second = await embedder.embed(["annual leave policy", "annual leave policy"])

    assert first == second
    assert len(first) == 64
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0)


async def test_hashing_embedder_ranks_shared_words_higher() -> None:
    embedder = HashingEmbedder()
    query, related, unrelated = await embedder.embed(
        ["how many days of annual leave", "staff get 20 days annual leave", "parking is free"]
    )

    assert _cosine(query, related) > _cosine(query, unrelated)


async def test_hashing_embedder_never_returns_a_zero_vector() -> None:
    (vector,) = await HashingEmbedder(dimensions=8).embed(["!!!"])

    assert any(vector)


def _embedding_response(request: httpx.Request, dimensions: int) -> httpx.Response:
    texts = json.loads(request.content)["input"]
    # Return the items out of order, as the API is allowed to.
    data = [
        {"index": i, "embedding": [float(len(text))] * dimensions}
        for i, text in reversed(list(enumerate(texts)))
    ]
    return httpx.Response(200, json={"data": data})


def _embedder(handler: httpx.MockTransport, *, batch_size: int = 2) -> OpenAIEmbedder:
    client = httpx.AsyncClient(transport=handler, base_url="https://api.test/v1")
    return OpenAIEmbedder(
        api_key="sk-test",
        model="text-embedding-3-small",
        batch_size=batch_size,
        dimensions=4,
        client=client,
    )


async def test_openai_embedder_batches_requests_and_keeps_order() -> None:
    batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        batches.append(json.loads(request.content)["input"])
        return _embedding_response(request, 4)

    embedder = _embedder(httpx.MockTransport(handler), batch_size=2)

    vectors = await embedder.embed(["a", "bb", "ccc", "dddd", "eeeee"])

    assert batches == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


async def test_openai_embedder_retries_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.ingestion.embeddings.asyncio.sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, headers={"retry-after": "1"})
        return _embedding_response(request, 4)

    vectors = await _embedder(httpx.MockTransport(handler)).embed(["hello"])

    assert calls == 3
    assert vectors == [[5.0] * 4]


async def test_openai_embedder_does_not_retry_bad_requests() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(EmbeddingError, match="401"):
        await _embedder(httpx.MockTransport(handler)).embed(["hello"])
    assert calls == 1


async def test_openai_embedder_rejects_wrong_vector_size() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _embedding_response(request, 3)

    with pytest.raises(EmbeddingError, match="size"):
        await _embedder(httpx.MockTransport(handler)).embed(["hello"])
