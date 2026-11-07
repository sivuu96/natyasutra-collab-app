from fastapi.testclient import TestClient


def test_health_is_up_without_database(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_reports_database_up(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "up"}


def test_request_id_is_echoed_or_generated(client: TestClient) -> None:
    generated = client.get("/health").headers["X-Request-ID"]
    assert len(generated) == 32

    echoed = client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"]
    assert echoed == "abc-123"
