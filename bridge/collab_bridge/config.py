"""Local bridge configuration: hub address, paired device, and the agents this machine offers.

Stored as JSON in the state directory. It never contains the device secret (see secrets_store).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
HARNESSES = ("claude_code", "codex")


class ConfigError(ValueError):
    pass


def default_state_dir() -> Path:
    override = os.environ.get("COLLAB_BRIDGE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".collab-bridge"


@dataclass
class AgentConfig:
    handle: str
    display_name: str
    harness: str
    description: str | None = None
    capabilities: list[str] = field(default_factory=lambda: ["chat", "read_repo"])
    repo_roots: list[str] = field(default_factory=list)
    default_repo: str | None = None
    # Optional explicit path to the harness executable (testing, or when it is not on PATH).
    harness_bin: str | None = None

    def validate(self) -> None:
        if not HANDLE_RE.match(self.handle):
            raise ConfigError(f"handle {self.handle!r} must be 3-40 lowercase letters, digits or hyphens")
        if self.harness not in HARNESSES:
            raise ConfigError(f"harness must be one of {', '.join(HARNESSES)}")
        if not self.display_name.strip():
            raise ConfigError("display name is required")
        if not self.repo_roots:
            raise ConfigError("at least one repository root is required; an agent cannot run outside one")
        roots = [Path(root).expanduser() for root in self.repo_roots]
        for root in roots:
            if not root.is_dir():
                raise ConfigError(f"repository root does not exist or is not a directory: {root}")
        if self.default_repo is not None and not any(
            _is_within(Path(self.default_repo).expanduser(), root) for root in roots
        ):
            raise ConfigError("default repository must be inside one of the repository roots")


@dataclass
class BridgeConfig:
    hub_url: str
    device_id: str | None = None
    device_name: str | None = None
    agents: list[AgentConfig] = field(default_factory=list)

    def agent(self, handle: str) -> AgentConfig | None:
        return next((a for a in self.agents if a.handle == handle), None)

    def validate(self) -> None:
        if not self.hub_url.startswith(("https://", "http://")):
            raise ConfigError("hub URL must start with https:// (or http:// for local development)")
        seen: set[str] = set()
        for agent in self.agents:
            agent.validate()
            if agent.handle in seen:
                raise ConfigError(f"duplicate agent handle {agent.handle}")
            seen.add(agent.handle)


def _is_within(path: Path, root: Path) -> bool:
    resolved = path.expanduser().resolve(strict=False)
    root_resolved = root.expanduser().resolve(strict=True)
    return resolved == root_resolved or root_resolved in resolved.parents


def config_path(state_dir: Path) -> Path:
    return state_dir / "config.json"


def load_config(state_dir: Path) -> BridgeConfig:
    path = config_path(state_dir)
    if not path.exists():
        raise ConfigError(f"no bridge configuration at {path}; run `collab-bridge pair` first")
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    config = BridgeConfig(
        hub_url=str(raw.get("hub_url", "")),
        device_id=raw.get("device_id"),
        device_name=raw.get("device_name"),
        agents=[AgentConfig(**item) for item in raw.get("agents", [])],
    )
    config.validate()
    return config


def save_config(state_dir: Path, config: BridgeConfig) -> None:
    config.validate()
    state_dir.mkdir(parents=True, exist_ok=True)
    path = config_path(state_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    tmp.replace(path)
