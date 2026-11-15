"""GitHub access: validation, the installation-token request, clone behaviour, and the repository run flow.

No test contacts GitHub. Token requests use a mock transport and clones use a local bare repository.
"""

# ruff: noqa: E501  (embedded fake-harness scripts are long string literals)

import base64
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from collab_runtime.github import (
    GitHubError,
    clone_repository,
    installation_token,
    validate_ref,
    validate_repo,
)
from collab_runtime.harness import ClaudeRunner
from collab_runtime.main import Config, make_repo_preparer, run
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from test_runtime import FakeHub, _write_fake_harness


@pytest.fixture(scope="module")
def app_key() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


# ---- validation -------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["owner", "a/b/c", "-x/y", "owner/name.git", "owner/../etc", "owner/ bad", ""])
def test_bad_repository_names_are_rejected(name: str) -> None:
    with pytest.raises(GitHubError) as error:
        validate_repo(name)
    assert error.value.code == "invalid_repository"


def test_good_repository_names_are_accepted() -> None:
    assert validate_repo("sivuu96/natyasutra-collab-app") == "sivuu96/natyasutra-collab-app"
    assert validate_repo("sivuu96/Natyasutra") == "sivuu96/Natyasutra"


@pytest.mark.parametrize("ref", ["-upload-pack=evil", "../main", "feature..x", "", "a b"])
def test_bad_refs_are_rejected(ref: str) -> None:
    with pytest.raises(GitHubError) as error:
        validate_ref(ref)
    assert error.value.code == "invalid_ref"


# ---- installation token -----------------------------------------------------------------------------


def test_the_installation_token_is_limited_to_one_repository_and_read_access(app_key: tuple[str, str]) -> None:
    private_pem, public_pem = app_key
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"token": "ghs_test_token"})

    token = installation_token(
        httpx.Client(transport=httpx.MockTransport(handler)),
        app_id="5698971",
        installation_id="180582474",
        private_key_pem=private_pem,
        repo_full_name="sivuu96/Natyasutra",
        now=1_700_000_000,
    )

    assert token == "ghs_test_token"
    assert seen["url"] == "https://api.github.com/app/installations/180582474/access_tokens"
    assert seen["body"] == {"repositories": ["Natyasutra"], "permissions": {"contents": "read"}}
    claims = jwt.decode(
        seen["auth"].removeprefix("Bearer "), public_pem, algorithms=["RS256"], options={"verify_exp": False}
    )
    assert claims["iss"] == "5698971"
    assert claims["exp"] - claims["iat"] <= 600  # GitHub allows at most ten minutes


def test_a_refused_token_request_is_an_error(app_key: tuple[str, str]) -> None:
    private_pem, _ = app_key
    http = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(404, json={"message": "Not Found"})))
    with pytest.raises(GitHubError) as error:
        installation_token(
            http,
            app_id="1",
            installation_id="2",
            private_key_pem=private_pem,
            repo_full_name="sivuu96/Natyasutra",
        )
    assert error.value.code == "github_token_refused"


# ---- clone ------------------------------------------------------------------------------------------


def _bare_remote(root: Path, owner: str, name: str, branch: str) -> Path:
    """A local bare repository laid out as <root>/<owner>/<name>.git, with one commit on `branch`."""
    work = root / "work"
    work.mkdir(parents=True)
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=test"]
    subprocess.run([*git, "init", "-q", "-b", branch, str(work)], check=True)
    (work / "README.md").write_text("hello from the repository\n", encoding="utf-8")
    subprocess.run([*git, "-C", str(work), "add", "."], check=True)
    subprocess.run([*git, "-C", str(work), "commit", "-q", "-m", "init"], check=True)
    bare = root / owner / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return root


def test_clone_fetches_only_the_requested_branch(tmp_path: Path) -> None:
    remote = _bare_remote(tmp_path / "remote", "sivuu96", "Natyasutra", "main")
    destination = tmp_path / "dest" / "repo"

    clone_repository(
        repo_full_name="sivuu96/Natyasutra",
        ref="main",
        token="",
        destination=str(destination),
        base_url=remote.as_posix(),
    )

    assert (destination / "README.md").read_text(encoding="utf-8") == "hello from the repository\n"


def test_a_missing_branch_is_a_clone_failure_and_the_token_is_not_echoed(tmp_path: Path) -> None:
    remote = _bare_remote(tmp_path / "remote", "sivuu96", "Natyasutra", "main")
    with pytest.raises(GitHubError) as error:
        clone_repository(
            repo_full_name="sivuu96/Natyasutra",
            ref="no-such-branch",
            token="ghs_secret_value_123",
            destination=str(tmp_path / "dest"),
            base_url=remote.as_posix(),
        )
    assert error.value.code == "clone_failed"
    assert "ghs_secret_value_123" not in (error.value.message or "")


def test_the_token_is_sent_as_a_header_not_in_the_clone_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("collab_runtime.github.subprocess.run", fake_run)
    clone_repository(
        repo_full_name="sivuu96/Natyasutra",
        ref="main",
        token="ghs_header_only",
        destination=str(tmp_path / "dest"),
    )

    command = captured[0]
    joined = " ".join(command)
    assert "ghs_header_only" not in joined.replace(base64.b64encode(b"x-access-token:ghs_header_only").decode(), "")
    assert any(part.startswith("http.extraHeader=Authorization: basic ") for part in command)
    assert command[-1].endswith("/sivuu96/Natyasutra.git") or command[-2].endswith("/sivuu96/Natyasutra.git")
    assert "credential.helper=" in command


# ---- the run flow with a repository ------------------------------------------------------------------

CWD_HARNESS = """
import json, os
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "cwd=" + os.getcwd()}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok", "session_id": "s"}), flush=True)
"""


def _config(binary: str) -> Config:
    return Config(
        hub_base_url="https://hub.example",
        project_id="natyasutra-agents",
        anthropic_secret_name="anthropic-api-key",
        claude_model="claude-haiku-5-5",
        claude_binary=binary,
        timeout_seconds=20,
        github_app_id="5698971",
        github_installation_id="180582474",
    )


def test_the_agent_runs_inside_the_cloned_repository(tmp_path: Path) -> None:
    hub = FakeHub(
        context={
            "prompt": "where am I",
            "harness": "claude_code",
            "runtime_token": "runtime-token",
            "repository": "sivuu96/Natyasutra",
            "ref": "main",
        }
    )
    cloned: list[tuple[str, str, str]] = []

    def prepare(repository: str, ref: str, destination: str) -> None:
        cloned.append((repository, ref, destination))
        Path(destination).mkdir(parents=True)
        (Path(destination) / "marker.txt").write_text("x", encoding="utf-8")

    code = run(
        _config(_write_fake_harness(tmp_path, CWD_HARNESS)),
        env={"RUN_LEASE": "lease", "CLOUD_RUN_EXECUTION": "projects/p/locations/us-west1/jobs/j/executions/e"},
        hub=hub,  # type: ignore[arg-type]
        read_secret=lambda _p, _n: "sk-ant-api03-TESTKEY123456",
        runner=ClaudeRunner(timeout_seconds=20),
        prepare_repo=prepare,
    )

    assert code == 0
    assert cloned == [("sivuu96/Natyasutra", "main", cloned[0][2])]
    output = "".join(event.get("text", "") for event in hub.events if event["type"] == "output")
    assert cloned[0][2].replace("\\", "/").rstrip("/") in output.replace("\\", "/")


def test_a_repository_that_cannot_be_prepared_fails_the_run_without_starting_the_agent(tmp_path: Path) -> None:
    hub = FakeHub(
        context={
            "prompt": "x",
            "harness": "claude_code",
            "runtime_token": "runtime-token",
            "repository": "sivuu96/Natyasutra",
            "ref": "main",
        }
    )

    def prepare(_repository: str, _ref: str, _destination: str) -> None:
        raise GitHubError("github_token_refused", "HTTP 404")

    code = run(
        _config(_write_fake_harness(tmp_path, CWD_HARNESS)),
        env={"RUN_LEASE": "lease", "CLOUD_RUN_EXECUTION": "x"},
        hub=hub,  # type: ignore[arg-type]
        read_secret=lambda _p, _n: "sk-ant-api03-TESTKEY123456",
        runner=ClaudeRunner(timeout_seconds=20),
        prepare_repo=prepare,
    )
    assert code == 1
    assert hub.events[-1]["code"] == "github_token_refused"
    assert all(event["type"] != "output" for event in hub.events)


def test_a_repository_run_without_repo_access_configured_fails_clearly(tmp_path: Path) -> None:
    hub = FakeHub(
        context={
            "prompt": "x",
            "harness": "claude_code",
            "runtime_token": "runtime-token",
            "repository": "sivuu96/Natyasutra",
        }
    )
    code = run(
        _config(_write_fake_harness(tmp_path, CWD_HARNESS)),
        env={"RUN_LEASE": "lease", "CLOUD_RUN_EXECUTION": "x"},
        hub=hub,  # type: ignore[arg-type]
        read_secret=lambda _p, _n: "sk-ant-api03-TESTKEY123456",
        runner=ClaudeRunner(timeout_seconds=20),
        prepare_repo=None,
    )
    assert code == 1
    assert hub.events[-1]["code"] == "repo_unavailable"


def test_the_preparer_refuses_when_github_is_not_configured(tmp_path: Path) -> None:
    config = Config(
        hub_base_url="https://hub.example",
        project_id="p",
        anthropic_secret_name="a",
        claude_model="m",
        claude_binary="claude",
        timeout_seconds=5,
    )
    prepare = make_repo_preparer(config, lambda _p, _n: "key", httpx.Client())
    with pytest.raises(GitHubError) as error:
        prepare("sivuu96/Natyasutra", "main", str(tmp_path / "repo"))
    assert error.value.code == "github_not_configured"


def test_shlex_splits_the_binary_setting_with_quoted_paths() -> None:
    assert shlex.split(f'"{sys.executable}" fake.py') == [sys.executable, "fake.py"]


def test_an_empty_repository_is_reported_without_a_clone_failure(tmp_path: Path) -> None:
    bare = tmp_path / "remote" / "sivuu96" / "Natyasutra.git"
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    destination = tmp_path / "dest" / "repo"

    cloned = clone_repository(
        repo_full_name="sivuu96/Natyasutra",
        ref="main",
        token="",
        destination=str(destination),
        base_url=(tmp_path / "remote").as_posix(),
    )

    assert cloned is False
    assert not (destination / ".git").exists()


def test_a_missing_branch_in_a_non_empty_repository_is_still_a_clone_failure(tmp_path: Path) -> None:
    remote = _bare_remote(tmp_path / "remote", "sivuu96", "Natyasutra", "main")
    with pytest.raises(GitHubError) as error:
        clone_repository(
            repo_full_name="sivuu96/Natyasutra",
            ref="develop",
            token="",
            destination=str(tmp_path / "dest"),
            base_url=remote.as_posix(),
        )
    assert error.value.code == "clone_failed"


PROMPT_ECHO_HARNESS = """
import json, sys
prompt = sys.argv[sys.argv.index("-p") + 1]
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": prompt}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok", "session_id": "s"}), flush=True)
"""


def test_a_run_on_an_empty_repository_completes_and_tells_the_agent(tmp_path: Path) -> None:
    hub = FakeHub(
        context={
            "prompt": "what is in this repo?",
            "harness": "claude_code",
            "runtime_token": "runtime-token",
            "repository": "sivuu96/Natyasutra",
            "ref": "main",
        }
    )

    def prepare(_repository: str, _ref: str, destination: str) -> bool:
        Path(destination).mkdir(parents=True, exist_ok=True)
        return False

    code = run(
        _config(_write_fake_harness(tmp_path, PROMPT_ECHO_HARNESS)),
        env={"RUN_LEASE": "lease", "CLOUD_RUN_EXECUTION": "x"},
        hub=hub,  # type: ignore[arg-type]
        read_secret=lambda _p, _n: "sk-ant-api03-TESTKEY123456",
        runner=ClaudeRunner(timeout_seconds=20),
        prepare_repo=prepare,
    )
    assert code == 0, hub.events
    assert hub.events[-1]["type"] == "completed"
    assert all(event["type"] != "failed" for event in hub.events)
    echoed = " ".join(event.get("text", "") for event in hub.events if event["type"] in ("output", "message"))
    assert "has no commits yet" in echoed
    assert "what is in this repo?" in echoed
