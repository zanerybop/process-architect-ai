"""Provision trusted fixtures locally, then exercise real login and CSRF checks."""

AUTH_HEADERS = {"X-Requested-With": "ProcessArchitect"}
TEST_CREDENTIALS = {"username": "api-tester", "password": "synthetic-test-password-42"}


def authorize(connection, credentials=None):
    credentials = credentials or TEST_CREDENTIALS
    connection.app.state.auth.create_user(**credentials)
    response = connection.post(
        "/api/auth/login", json=credentials, headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    connection.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


async def authorize_async(connection, app):
    app.state.auth.create_user(**TEST_CREDENTIALS)
    response = await connection.post("/api/auth/login", json=TEST_CREDENTIALS, headers=AUTH_HEADERS)
    assert response.status_code == 200, response.text
    connection.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()
