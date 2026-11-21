"""Committing and pushing an agent's changes, against a local bare repository, and the write token's scope."""

import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest

from collab_runtime.github import clone_repository, installation_token
from collab_runtime.harness import build_claude_args, git_dir_denials, tool_rules
from collab_runtime.publish import PublishError, commit_and_push, has_changes

GIT = ["git", "-c", "user.email=t@example.com", "-c", "user.name=test"]


def _remote(root: Path) -> Path:
    """A bare repository laid out as <root>/owner/name.git, with `main` and `feature-x`."""
    work = root / "seed"
    work.mkdir(parents=True)
    subprocess.run([*GIT, "init", "-q", "-b", "main", str(work)], check=True)
    (work / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run([*GIT, "-C", str(work), "add", "."], check=True)
    subprocess.run([*GIT, "-C", str(work), "commit", "-q", "-m", "init"], check=True)
    subprocess.run([*GIT, "-C", str(work), "branch", "feature-x"], check=True)
    bare = root / "owner" / "name.git"
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return root


def _clone(remote: Path, ref: str, into: Path) -> str:
    assert clone_repository(
        repo_full_name="owner/name", ref=ref, token="", destination=str(into), base_url=remote.as_posix()
    )
    return str(into)


def _remote_head(remote: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(remote / "owner" / "name.git"), "rev-parse", f"refs/heads/{ref}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _push(folder: str, path: str, content: str, branch: str = "feature-x") -> None:
    """Simulates someone else pushing to the branch after the agent's clone was made."""
    Path(folder, path).write_text(content, encoding="utf-8")
    subprocess.run([*GIT, "-C", folder, "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", folder, "commit", "-q", "-m", "other"], check=True)
    subprocess.run(["git", "-C", folder, "push", "-q", "origin", f"HEAD:refs/heads/{branch}"], check=True)


def test_an_agent_change_is_committed_and_pushed_to_its_own_branch(tmp_path: Path) -> None:
    remote = _remote(tmp_path / "remote")
    clone = _clone(remote, "feature-x", tmp_path / "clone")
    Path(clone, "docs.md").write_text("new section\n", encoding="utf-8")

    sha = commit_and_push(
        clone,
        repo_full_name="owner/name",
        ref="feature-x",
        token="write-token-value",
        message="Changes by @claude (run 12345678)",
        base_url=remote.as_posix(),
    )

    assert sha is not None
    assert _remote_head(remote, "feature-x") == sha
    assert _remote_head(remote, "main") != sha  # main is untouched


def test_no_change_means_no_commit_and_no_push(tmp_path: Path) -> None:
    remote = _remote(tmp_path / "remote")
    clone = _clone(remote, "feature-x", tmp_path / "clone")
    before = _remote_head(remote, "feature-x")

    assert not has_changes(clone)
    assert commit_and_push(clone, repo_full_name="owner/name", ref="feature-x", token="t", message="m",
                           base_url=remote.as_posix()) is None
    assert _remote_head(remote, "feature-x") == before


def test_main_is_never_pushed_to(tmp_path: Path) -> None:
    remote = _remote(tmp_path / "remote")
    clone = _clone(remote, "main", tmp_path / "clone")
    Path(clone, "docs.md").write_text("x\n", encoding="utf-8")
    before = _remote_head(remote, "main")

    with pytest.raises(PublishError) as error:
        commit_and_push(clone, repo_full_name="owner/name", ref="main", token="t", message="m",
                        base_url=remote.as_posix())
    assert error.value.code == "protected_branch"
    assert _remote_head(remote, "main") == before


def test_a_branch_that_moved_is_not_overwritten(tmp_path: Path) -> None:
    remote = _remote(tmp_path / "remote")
    clone = _clone(remote, "feature-x", tmp_path / "clone")
    other = _clone(remote, "feature-x", tmp_path / "other")
    _push(other, "theirs.md", "from someone else\n")
    before = _remote_head(remote, "feature-x")
    Path(clone, "mine.md").write_text("from the agent\n", encoding="utf-8")

    with pytest.raises(PublishError) as error:
        commit_and_push(clone, repo_full_name="owner/name", ref="feature-x", token="t", message="m",
                        base_url=remote.as_posix())
    assert error.value.code == "push_rejected"
    assert _remote_head(remote, "feature-x") == before


@pytest.mark.parametrize("name", [".env", "deploy/server.pem", "keys/id_rsa.key"])
def test_secret_looking_files_are_never_committed(tmp_path: Path, name: str) -> None:
    remote = _remote(tmp_path / "remote")
    clone = _clone(remote, "feature-x", tmp_path / "clone")
    target = Path(clone, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("SECRET=1\n", encoding="utf-8")
    before = _remote_head(remote, "feature-x")

    with pytest.raises(PublishError) as error:
        commit_and_push(clone, repo_full_name="owner/name", ref="feature-x", token="t", message="m",
                        base_url=remote.as_posix())
    assert error.value.code == "forbidden_file"
    assert _remote_head(remote, "feature-x") == before


@pytest.mark.skipif(os.name == "nt", reason="Windows needs a privilege to create symbolic links; run on Linux")
def test_a_symbolic_link_is_never_committed(tmp_path: Path) -> None:
    remote = _remote(tmp_path / "remote")
    clone = _clone(remote, "feature-x", tmp_path / "clone")
    Path(clone, "link").symlink_to("/etc/passwd")

    with pytest.raises(PublishError) as error:
        commit_and_push(clone, repo_full_name="owner/name", ref="feature-x", token="t", message="m",
                        base_url=remote.as_posix())
    assert error.value.code == "symlink"


def test_the_write_token_never_appears_in_an_error(tmp_path: Path) -> None:
    remote = _remote(tmp_path / "remote")
    clone = _clone(remote, "feature-x", tmp_path / "clone")
    other = _clone(remote, "feature-x", tmp_path / "other")
    _push(other, "theirs.md", "x\n")
    Path(clone, "mine.md").write_text("y\n", encoding="utf-8")

    with pytest.raises(PublishError) as error:
        commit_and_push(clone, repo_full_name="owner/name", ref="feature-x", token="SECRET-WRITE-TOKEN",
                        message="m", base_url=remote.as_posix())
    assert "SECRET-WRITE-TOKEN" not in error.value.message
    assert "SECRET-WRITE-TOKEN" not in str(error.value)


def test_the_write_token_is_a_contents_write_token_for_one_repository() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"token": "t"})

    installation_token(
        httpx.Client(transport=httpx.MockTransport(handler)),
        app_id="1",
        installation_id="2",
        private_key_pem=pem,
        repo_full_name="owner/name",
        write=True,
    )
    assert seen["body"] == {"repositories": ["name"], "permissions": {"contents": "write"}}


def test_a_write_run_may_edit_its_folder_but_not_git_and_a_read_run_may_not_edit(tmp_path: Path) -> None:
    folder = str(tmp_path / "repo")
    read_only = build_claude_args(["claude"], model="m", prompt="p", folder=folder)
    writing = build_claude_args(["claude"], model="m", prompt="p", folder=folder, write=True)

    assert not any(rule.startswith(("Edit(", "Write(")) for rule in tool_rules(folder))
    assert not any(part.startswith(("Edit(", "Write(")) for part in read_only)
    allowed = writing[writing.index("--allowedTools") + 1 : writing.index("--disallowedTools")]
    assert any(rule.startswith("Edit(") for rule in allowed)
    assert all(rule in writing for rule in git_dir_denials(folder))
    assert writing[writing.index("--disallowedTools") + 1 : writing.index("--disallowedTools") + 3] == git_dir_denials(folder)


def test_a_clone_without_changes_reports_none_so_the_reply_stays_as_written(tmp_path: Path) -> None:
    remote = _remote(tmp_path / "remote")
    clone = _clone(remote, "feature-x", tmp_path / "clone")
    assert not has_changes(clone)
