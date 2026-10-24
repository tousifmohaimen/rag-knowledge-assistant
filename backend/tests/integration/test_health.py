from httpx import AsyncClient


async def test_health_reports_database_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.headers["X-Request-ID"]


async def test_safe_incoming_request_id_is_kept(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "trace-123"})

    assert response.headers["X-Request-ID"] == "trace-123"


async def test_unsafe_incoming_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "bad id\nwith newline"})

    assert response.headers["X-Request-ID"] != "bad id\nwith newline"
