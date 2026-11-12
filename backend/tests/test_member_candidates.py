"""Suggestions when adding a person: any account not already in the project, for admins only."""

from collections.abc import Callable

from fastapi.testclient import TestClient
from helpers import API, add_member, create_project, register


def test_suggestions_find_any_account_not_already_in_the_project(
    client_factory: Callable[[], TestClient],
) -> None:
    owner = client_factory()
    register(owner, "cand-owner@example.com", "Owner")
    project_one = create_project(owner, "Candidate one")["id"]
    project_two = create_project(owner, "Candidate two")["id"]

    teammate = client_factory()
    register(teammate, "cand-teammate@example.com", "Teammate")
    add_member(owner, project_two, "cand-teammate@example.com")

    already_in = client_factory()
    register(already_in, "cand-inside@example.com", "Inside")
    add_member(owner, project_one, "cand-inside@example.com")

    stranger = client_factory()
    register(stranger, "cand-stranger@example.com", "Stranger")

    def search(term: str) -> list[str]:
        response = owner.get(f"{API}/projects/{project_one}/member-candidates", params={"q": term})
        assert response.status_code == 200, response.text
        return [item["email"] for item in response.json()]

    assert search("cand-teammate") == ["cand-teammate@example.com"]
    assert search("cand-stranger") == ["cand-stranger@example.com"]  # any account can be found
    assert search("cand-inside") == []  # already a member of this project
    assert search("c") == []  # too short to suggest


def test_only_admins_see_suggestions(client_factory: Callable[[], TestClient]) -> None:
    owner = client_factory()
    register(owner, "cand-owner2@example.com", "Owner")
    project = create_project(owner, "Candidate private")["id"]
    member = client_factory()
    register(member, "cand-member2@example.com", "Member")
    add_member(owner, project, "cand-member2@example.com", role="member")

    response = member.get(f"{API}/projects/{project}/member-candidates", params={"q": "cand"})

    assert response.status_code == 403
