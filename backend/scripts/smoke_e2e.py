"""End-to-end smoke test against a RUNNING stack (API + frontend dev proxy or production origin).

Exercises the real path a browser uses: register two users, create a project, add a member,
post a message with a mention, create a task, and confirm the owner's WebSocket receives both
events live. Uses unique throwaway accounts on every run.

Usage (from backend/, with the API and frontend dev server running):
    .venv\\Scripts\\python.exe -m scripts.smoke_e2e --base http://localhost:5173

Requires: pip install websockets httpx (both present in the backend venv).
"""

import argparse
import asyncio
import json
import secrets
import sys

import httpx
from websockets.asyncio.client import connect


def csrf(client: httpx.Client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("ns_csrf", "")}


def expect(response: httpx.Response, status: int, what: str) -> dict:
    if response.status_code != status:
        raise SystemExit(f"FAILED {what}: expected {status}, got {response.status_code}: {response.text}")
    print(f"ok  {what} -> {status}")
    return response.json() if response.content and response.status_code != 204 else {}


async def run(base: str) -> None:
    api = f"{base}/api/v1"
    password = secrets.token_urlsafe(16)
    suffix = secrets.token_hex(3)
    owner_email = f"smoke-owner-{suffix}@example.com"
    member_email = f"smoke-member-{suffix}@example.com"

    with httpx.Client(timeout=15) as owner, httpx.Client(timeout=15) as member:
        expect(owner.get(f"{api}/auth/me"), 401, "anonymous /auth/me (sets CSRF cookie)")
        expect(member.get(f"{api}/auth/me"), 401, "anonymous /auth/me for member")

        expect(
            owner.post(
                f"{api}/auth/register",
                headers=csrf(owner),
                json={"email": owner_email, "display_name": "Smoke Owner", "password": password},
            ),
            201,
            "register owner",
        )
        expect(
            member.post(
                f"{api}/auth/register",
                headers=csrf(member),
                json={"email": member_email, "display_name": "Smoke Member", "password": password},
            ),
            201,
            "register member",
        )

        team = expect(
            owner.post(f"{api}/teams", headers=csrf(owner), json={"name": f"Smoke {suffix}"}), 201, "create team"
        )
        project = expect(
            owner.post(
                f"{api}/teams/{team['id']}/projects", headers=csrf(owner), json={"name": f"Smoke Project {suffix}"}
            ),
            201,
            "create project",
        )
        pid = project["id"]
        expect(
            owner.post(
                f"{api}/projects/{pid}/members", headers=csrf(owner), json={"email": member_email, "role": "member"}
            ),
            201,
            "add member to project",
        )

        channels = expect(owner.get(f"{api}/projects/{pid}/channels"), 200, "list channels")
        general = next(c["id"] for c in channels if c["name"] == "general")

        session_cookie = owner.cookies.get("ns_session")
        if not session_cookie:
            raise SystemExit("FAILED: no session cookie for owner")
        ws_url = base.replace("http", "ws", 1) + f"/api/v1/ws/projects/{pid}?since=0"
        async with connect(ws_url, additional_headers={"Origin": base, "Cookie": f"ns_session={session_cookie}"}) as ws:
            print(f"ok  websocket connected through proxy: {ws_url.split('?')[0]}")

            expect(
                member.post(
                    f"{api}/projects/{pid}/channels/{general}/messages",
                    headers=csrf(member),
                    json={"body": f"Hello from smoke @smoke-owner-{suffix}"},
                ),
                201,
                "member posts message",
            )
            live_message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if live_message["event_type"] != "conversation.message.created":
                raise SystemExit(f"FAILED: expected message event, got {live_message}")
            print(f"ok  owner websocket received {live_message['event_type']} seq={live_message['sequence']}")

            expect(
                member.post(
                    f"{api}/projects/{pid}/tasks", headers=csrf(member), json={"title": f"Smoke task {suffix}"}
                ),
                201,
                "member creates task",
            )
            live_task = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if live_task["event_type"] != "task.created":
                raise SystemExit(f"FAILED: expected task event, got {live_task}")
            print(f"ok  owner websocket received {live_task['event_type']} seq={live_task['sequence']}")

        expect(owner.post(f"{api}/auth/logout", headers=csrf(owner)), 204, "owner logs out")
        expect(owner.get(f"{api}/auth/me"), 401, "owner session revoked after logout")

    print("\nSmoke test passed.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:5173")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.base.rstrip("/")))
    except (httpx.HTTPError, TimeoutError, OSError) as error:
        print(f"FAILED: {error!r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
