from httpx import AsyncClient

from tests.conftest import MakeUser


async def _login(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body: dict[str, str] = response.json()
    return body


async def test_login_returns_access_and_refresh_tokens(
    client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()

    body = await _login(client, user.email, user.password)

    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]


async def test_login_email_is_case_insensitive(client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user(email="admin@acme.com")

    await _login(client, "ADMIN@acme.com", user.password)


async def test_wrong_password_and_unknown_email_get_the_same_error(
    client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()

    wrong_password = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "not-the-password"}
    )
    unknown_email = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@acme.com", "password": "whatever"}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_disabled_user_cannot_log_in(client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user(is_active=False)

    response = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": user.password}
    )

    assert response.status_code == 401


async def test_me_returns_the_logged_in_user(client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user()
    tokens = await _login(client, user.email, user.password)

    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "email": user.email,
        "role": "admin",
    }


async def test_me_without_a_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


async def test_refresh_token_cannot_be_used_as_access_token(
    client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()
    tokens = await _login(client, user.email, user.password)

    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
    )

    assert response.status_code == 401


async def test_refresh_issues_a_working_access_token(
    client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()
    tokens = await _login(client, user.email, user.password)

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200
    new_access = response.json()["access_token"]

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200


async def test_access_token_cannot_be_used_to_refresh(
    client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()
    tokens = await _login(client, user.email, user.password)

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )

    assert response.status_code == 401
