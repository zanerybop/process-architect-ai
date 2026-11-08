from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


def test_health_reports_missing_worker_without_secret_or_fallback(tmp_path):
    class UnavailableRunner:
        async def health(self):
            return False

    settings = Settings(_env_file=None, data_dir=tmp_path, llm_api_key="test-private-secret")
    app = create_app(settings=settings, runner=UnavailableRunner())
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["dependencies"]["worker"] is False
    assert response.json()["generation_available"] is False
    assert "test-private-secret" not in response.text
