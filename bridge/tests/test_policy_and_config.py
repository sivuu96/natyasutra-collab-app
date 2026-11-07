import os
import subprocess
from pathlib import Path

import pytest

from collab_bridge.config import AgentConfig, BridgeConfig, ConfigError, load_config, save_config
from collab_bridge.policy import PolicyError, allowed_tools_for, resolve_working_directory, tools_needing_approval


def _agent(root: Path, default: Path | None = None, **overrides) -> AgentConfig:  # type: ignore[no-untyped-def]
    values = dict(
        handle="claude-architecture",
        display_name="Claude",
        harness="claude_code",
        repo_roots=[str(root)],
        default_repo=str(default or root),
    )
    values.update(overrides)
    return AgentConfig(**values)


def test_working_directory_inside_root_is_allowed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    agent = _agent(repo, default=repo / "src")
    assert resolve_working_directory(agent) == (repo / "src").resolve()


def test_working_directory_outside_roots_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "elsewhere"
    repo.mkdir()
    outside.mkdir()
    agent = _agent(repo, default=outside)
    with pytest.raises(PolicyError):
        resolve_working_directory(agent)


def test_prefix_lookalike_directory_is_not_inside_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    lookalike = tmp_path / "repo-evil"
    repo.mkdir()
    lookalike.mkdir()
    with pytest.raises(PolicyError):
        resolve_working_directory(_agent(repo, default=lookalike))


def test_symlink_escaping_root_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "secrets"
    repo.mkdir()
    outside.mkdir()
    link = repo / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("symlink creation not permitted on this platform")
        # Windows without developer mode: a directory junction exercises the same containment check.
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False
        )
        if completed.returncode != 0:
            pytest.skip("neither symlinks nor junctions are available")
    with pytest.raises(PolicyError):
        resolve_working_directory(_agent(repo, default=link))


def test_missing_default_repo_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(PolicyError):
        resolve_working_directory(_agent(repo, default=repo / "gone"))


def test_read_only_tools_do_not_need_approval() -> None:
    assert tools_needing_approval(["Read", "Grep", "Glob"]) == []
    assert tools_needing_approval(["Write", "Bash(git commit:*)", "Read"]) == ["Bash", "Write"]


def test_allowed_tools_grow_only_by_approved_names() -> None:
    assert allowed_tools_for([]) == ["Glob", "Grep", "Read"]
    assert allowed_tools_for(["Edit"]) == ["Edit", "Glob", "Grep", "Read"]


def test_agent_validation_rules(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        _agent(tmp_path, handle="Bad Handle").validate()
    with pytest.raises(ConfigError):
        _agent(tmp_path, harness="other").validate()
    with pytest.raises(ConfigError):
        _agent(tmp_path, repo_roots=[]).validate()
    with pytest.raises(ConfigError):
        _agent(tmp_path, repo_roots=[str(tmp_path / "missing")]).validate()
    with pytest.raises(ConfigError):
        _agent(tmp_path, default=tmp_path.parent).validate()


def test_config_round_trip_and_duplicate_handles_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = BridgeConfig(hub_url="https://hub.example", device_id="d1", device_name="Laptop", agents=[_agent(repo)])
    save_config(tmp_path / "state", config)
    loaded = load_config(tmp_path / "state")
    assert loaded.agents[0].handle == "claude-architecture"
    assert "device_secret" not in (tmp_path / "state" / "config.json").read_text(encoding="utf-8")

    config.agents.append(_agent(repo))
    with pytest.raises(ConfigError):
        config.validate()


def test_hub_url_must_be_http_or_https() -> None:
    with pytest.raises(ConfigError):
        BridgeConfig(hub_url="ftp://hub").validate()
