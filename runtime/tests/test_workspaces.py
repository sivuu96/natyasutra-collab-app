"""The workspace registry on a warm instance: creation, idle removal, the cap, and what it refuses."""

import os
import uuid
from pathlib import Path

import pytest

from collab_runtime.workspaces import Workspaces


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _make(tmp_path: Path, clock: Clock, **kwargs: int) -> Workspaces:
    return Workspaces(str(tmp_path / "ws"), clock=clock, **kwargs)


def test_create_makes_a_home_and_a_repo_folder_under_the_session(tmp_path: Path) -> None:
    clock = Clock()
    workspaces = _make(tmp_path, clock)
    session = str(uuid.uuid4())
    item = workspaces.create(session)
    assert os.path.isdir(item.home) and os.path.isdir(item.repo)
    assert item.repo.startswith(os.path.join(str(tmp_path / "ws"), session))
    assert item.last_invocation_id is None
    assert workspaces.get(session) is item


def test_each_new_workspace_has_its_own_conversation_id(tmp_path: Path) -> None:
    clock = Clock()
    workspaces = _make(tmp_path, clock)
    a = workspaces.create(str(uuid.uuid4()))
    b = workspaces.create(str(uuid.uuid4()))
    assert a.cli_session_id != b.cli_session_id


def test_record_success_remembers_the_last_completed_turn(tmp_path: Path) -> None:
    clock = Clock()
    workspaces = _make(tmp_path, clock)
    session, turn = str(uuid.uuid4()), str(uuid.uuid4())
    workspaces.create(session)
    workspaces.record_success(session, turn)
    assert workspaces.get(session).last_invocation_id == turn  # type: ignore[union-attr]


def test_discard_deletes_the_folder(tmp_path: Path) -> None:
    clock = Clock()
    workspaces = _make(tmp_path, clock)
    session = str(uuid.uuid4())
    item = workspaces.create(session)
    workspaces.discard(session)
    assert workspaces.get(session) is None
    assert not os.path.exists(item.root)


def test_idle_workspaces_are_removed_on_the_next_access(tmp_path: Path) -> None:
    clock = Clock()
    workspaces = _make(tmp_path, clock, idle_seconds=60)
    session = str(uuid.uuid4())
    item = workspaces.create(session)
    clock.now += 61
    assert workspaces.get(session) is None
    assert not os.path.exists(item.root)


def test_a_recently_used_workspace_survives_until_it_goes_idle(tmp_path: Path) -> None:
    clock = Clock()
    workspaces = _make(tmp_path, clock, idle_seconds=60)
    session = str(uuid.uuid4())
    workspaces.create(session)
    clock.now += 50
    workspaces.record_success(session, str(uuid.uuid4()))  # use refreshes the timer
    clock.now += 50
    assert workspaces.get(session) is not None


def test_the_cap_evicts_the_least_recently_used_session(tmp_path: Path) -> None:
    clock = Clock()
    workspaces = _make(tmp_path, clock, max_workspaces=2)
    first, second, third = (str(uuid.uuid4()) for _ in range(3))
    old = workspaces.create(first)
    clock.now += 1
    workspaces.create(second)
    clock.now += 1
    workspaces.create(third)  # over the cap: `first` is the oldest
    assert not os.path.exists(old.root)
    assert workspaces.sessions() == sorted([second, third])


def test_a_workspace_whose_repo_folder_vanished_is_not_reused(tmp_path: Path) -> None:
    clock = Clock()
    workspaces = _make(tmp_path, clock)
    session = str(uuid.uuid4())
    item = workspaces.create(session)
    os.rmdir(item.repo)
    assert workspaces.get(session) is None


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "not-a-uuid", "C:\\temp"])
def test_session_ids_that_are_not_uuids_are_refused(tmp_path: Path, bad: str) -> None:
    clock = Clock()
    workspaces = _make(tmp_path, clock)
    with pytest.raises(ValueError):
        workspaces.create(bad)
    with pytest.raises(ValueError):
        workspaces.get(bad)
    assert not (tmp_path / "escape").exists()
