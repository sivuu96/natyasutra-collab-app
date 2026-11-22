"""The saved state of a session: what is packed, what is restored, and what is refused."""

import io
import os
import tarfile

import pytest

from collab_runtime.harness import build_claude_args
from collab_runtime.session_store import ArchiveError, object_name, pack, unpack

SID = "6f1c2b8e-1111-4a2b-9c3d-000000000001"


def _make_state(workdir: str) -> None:
    repo = os.path.join(workdir, "repo")
    os.makedirs(os.path.join(repo, "docs"))
    with open(os.path.join(repo, "docs", "plan.md"), "w", encoding="utf-8") as f:
        f.write("draft")
    os.makedirs(os.path.join(repo, ".git"))
    with open(os.path.join(repo, ".git", "HEAD"), "w", encoding="utf-8") as f:
        f.write("ref: refs/heads/main")
    projects = os.path.join(workdir, "home", ".claude", "projects", "-work-repo")
    os.makedirs(projects)
    with open(os.path.join(projects, f"{SID}.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"type":"user"}\n')
    with open(os.path.join(workdir, "home", ".claude.json"), "w", encoding="utf-8") as f:
        f.write("{}")


def test_the_state_round_trips_to_a_new_folder(tmp_path) -> None:
    source = tmp_path / "source"
    _make_state(str(source))
    archive = pack(str(source))

    target = tmp_path / "target"
    unpack(archive, str(target))

    assert (target / "repo" / "docs" / "plan.md").read_text(encoding="utf-8") == "draft"
    assert (target / "repo" / ".git" / "HEAD").exists()
    assert (target / "home" / ".claude" / "projects" / "-work-repo" / f"{SID}.jsonl").exists()
    assert (target / "home" / ".claude.json").exists()


def test_links_in_the_repository_are_left_out_of_the_archive(tmp_path) -> None:
    source = tmp_path / "source"
    _make_state(str(source))
    try:
        os.symlink("/etc/passwd", source / "repo" / "link")
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create symlinks")

    with tarfile.open(fileobj=io.BytesIO(pack(str(source))), mode="r:gz") as archive:
        names = archive.getnames()
    assert "repo/docs/plan.md" in names
    assert "repo/link" not in names


def test_an_archive_with_a_path_outside_the_state_is_refused(tmp_path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        data = b"x"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    with pytest.raises(ArchiveError) as refused:
        unpack(buffer.getvalue(), str(tmp_path / "target"))
    assert refused.value.code == "unsafe_archive"
    assert not (tmp_path / "escape.txt").exists()


def test_an_archive_with_a_link_is_refused(tmp_path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("repo/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)

    with pytest.raises(ArchiveError):
        unpack(buffer.getvalue(), str(tmp_path / "target"))


def test_a_session_id_that_is_not_a_uuid_has_no_object_name() -> None:
    with pytest.raises(ArchiveError):
        object_name("../../etc")
    assert object_name(SID) == f"sessions/{SID}.tar.gz"


def test_a_resumed_turn_uses_resume_and_a_first_turn_uses_session_id() -> None:
    first = build_claude_args(["claude"], model="m", prompt="hi", folder="/w/repo", session_id=SID, resume=False)
    later = build_claude_args(["claude"], model="m", prompt="hi", folder="/w/repo", session_id=SID, resume=True)
    none = build_claude_args(["claude"], model="m", prompt="hi", folder="/w/repo")

    assert first[first.index("--session-id") + 1] == SID and "--resume" not in first
    assert later[later.index("--resume") + 1] == SID and "--session-id" not in later
    assert "--resume" not in none and "--session-id" not in none
