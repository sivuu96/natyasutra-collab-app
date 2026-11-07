"""Shared HTTP helpers for API tests."""

from typing import Any

from fastapi.testclient import TestClient

PASSWORD = "correct horse battery staple"
API = "/api/v1"


def csrf_headers(client: TestClient) -> dict[str, str]:
    if "ns_csrf" not in client.cookies:
        client.get("/health")
    return {"X-CSRF-Token": client.cookies["ns_csrf"]}


def register(client: TestClient, email: str, name: str | None = None, password: str = PASSWORD) -> dict[str, Any]:
    response = client.post(
        f"{API}/auth/register",
        json={"email": email, "display_name": name or email.split("@")[0], "password": password},
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = client.post(
        f"{API}/auth/login", json={"email": email, "password": password}, headers=csrf_headers(client)
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_project(client: TestClient, name: str = "Port NatyaSutra") -> dict[str, Any]:
    team = client.post(f"{API}/teams", json={"name": f"{name} team"}, headers=csrf_headers(client))
    assert team.status_code == 201, team.text
    project = client.post(
        f"{API}/teams/{team.json()['id']}/projects", json={"name": name}, headers=csrf_headers(client)
    )
    assert project.status_code == 201, project.text
    return project.json()


def general_channel(client: TestClient, project_id: str) -> str:
    channels = client.get(f"{API}/projects/{project_id}/channels").json()
    return next(c["id"] for c in channels if c["name"] == "general")


def add_member(client: TestClient, project_id: str, email: str, role: str = "member") -> dict[str, Any]:
    response = client.post(
        f"{API}/projects/{project_id}/members", json={"email": email, "role": role}, headers=csrf_headers(client)
    )
    assert response.status_code == 201, response.text
    return response.json()
