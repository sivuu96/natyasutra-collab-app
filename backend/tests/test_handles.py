"""Person handles: assigned at sign-up, listed with members, and kept distinct from agent handles."""


from fastapi.testclient import TestClient
from helpers import API, create_project, register
from sqlalchemy import select

from app.models.bridge import Agent
from app.models.identity import User
from app.services.handles import allocate_user_handle, base_handle, handle_taken


def test_base_handles_come_from_the_email() -> None:
    assert base_handle("Sivsankar@gmail.com") == "sivsankar"
    assert base_handle("nandu.nandakumar@gmail.com") == "nandu-nandakumar"
    assert base_handle("a@b.c") == "usera"


def test_a_taken_handle_gets_a_number(engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(email="pat@one.test", display_name="Pat", handle="pat"))
        db.commit()
        assert allocate_user_handle(db, "pat@two.test") == "pat-2"


def test_a_person_handle_blocks_the_same_agent_handle(engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(email="sam@x.test", display_name="Sam", handle="sam"))
        db.commit()
        assert handle_taken(db, "sam")
        assert not handle_taken(db, "someone-else")


def test_registration_assigns_a_handle_and_members_list_it(client: TestClient) -> None:
    owner = register(client, "lead@example.com", "Lead")
    project = create_project(client, "Handles project")
    members = client.get(f"{API}/projects/{project['id']}/members").json()
    mine = next(m for m in members if m["user_id"] == owner["id"])
    assert mine["handle"] == "lead"


def test_an_agent_cannot_take_a_person_handle(engine, client: TestClient) -> None:  # type: ignore[no-untyped-def]
    register(client, "taken@example.com", "Taken")
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=engine)() as db:
        user = db.scalar(select(User).where(User.email == "taken@example.com"))
        assert user is not None and user.handle == "taken"
        assert db.scalar(select(Agent.id).where(Agent.handle == "taken")) is None
        assert handle_taken(db, "taken")
