"""Command-line interface for the local bridge.

    collab-bridge pair --hub https://hub.example --code ABCD-EFGH --name "Laptop"
    collab-bridge agent add --handle claude-architecture --display "Claude (architecture)" \
        --harness claude_code --repo C:\\work\\natyasutra
    collab-bridge agent list
    collab-bridge status
    collab-bridge run
    collab-bridge forget            # removes the local secret and configuration
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

from collab_bridge.adapters.base import AgentAdapter
from collab_bridge.adapters.claude_code import ClaudeCodeAdapter
from collab_bridge.adapters.codex import CodexAdapter
from collab_bridge.adapters.fake import FakeAdapter
from collab_bridge.client import BridgeClient
from collab_bridge.config import (
    AgentConfig,
    BridgeConfig,
    ConfigError,
    default_state_dir,
    load_config,
    save_config,
)
from collab_bridge.secrets_store import delete_device_secret, load_device_secret, store_device_secret
from collab_bridge.state import BridgeState

__version__ = "0.1.0"


def build_adapters(include_fake: bool = False) -> dict[str, AgentAdapter]:
    adapters: dict[str, AgentAdapter] = {"claude_code": ClaudeCodeAdapter(), "codex": CodexAdapter()}
    if include_fake:
        adapters["fake"] = FakeAdapter()
    return adapters


def _state_dir(args: argparse.Namespace) -> Path:
    return Path(args.state_dir).expanduser() if args.state_dir else default_state_dir()


def cmd_pair(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    hub = args.hub.rstrip("/")
    try:
        response = httpx.post(
            f"{hub}/api/v1/bridge/pair", timeout=20, json={"code": args.code, "device_name": args.name}
        )
    except httpx.HTTPError as error:
        print(f"Could not reach the hub: {error.__class__.__name__}", file=sys.stderr)
        return 2
    if response.status_code != 201:
        detail = (
            response.json().get("detail", "pairing failed")
            if response.headers.get("content-type", "").startswith("application/json")
            else "pairing failed"
        )
        print(f"Pairing failed: {detail}", file=sys.stderr)
        return 1
    body = response.json()
    device_id = str(body["device_id"])
    where = store_device_secret(state_dir, device_id, str(body["device_secret"]))
    config = BridgeConfig(hub_url=hub, device_id=device_id, device_name=args.name, agents=[])
    save_config(state_dir, config)
    print(f"Paired device {args.name!r}. Secret stored in: {where}.")
    print("Next: add an agent with `collab-bridge agent add ...`, then run `collab-bridge run`.")
    return 0


def cmd_agent_add(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    config = load_config(state_dir)
    if config.agent(args.handle) is not None:
        print(f"An agent named {args.handle} already exists on this machine.", file=sys.stderr)
        return 1
    repos = [str(Path(r).expanduser().resolve()) for r in args.repo]
    agent = AgentConfig(
        handle=args.handle,
        display_name=args.display,
        harness=args.harness,
        description=args.description,
        repo_roots=repos,
        default_repo=str(Path(args.default_repo).expanduser().resolve()) if args.default_repo else repos[0],
        harness_bin=args.harness_bin,
    )
    try:
        agent.validate()
    except ConfigError as error:
        print(f"Invalid agent: {error}", file=sys.stderr)
        return 1
    config.agents.append(agent)
    save_config(state_dir, config)
    print(f"Added agent {agent.handle} ({agent.harness}). Repository roots: {', '.join(repos)}.")
    return 0


def cmd_agent_list(args: argparse.Namespace) -> int:
    config = load_config(_state_dir(args))
    if not config.agents:
        print("No agents configured. Add one with `collab-bridge agent add`.")
        return 0
    for agent in config.agents:
        print(f"{agent.handle:24} {agent.harness:12} repo={agent.default_repo}")
    return 0


def cmd_agent_remove(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    config = load_config(state_dir)
    before = len(config.agents)
    config.agents = [a for a in config.agents if a.handle != args.handle]
    if len(config.agents) == before:
        print(f"No agent named {args.handle}.", file=sys.stderr)
        return 1
    save_config(state_dir, config)
    print(f"Removed {args.handle} locally. Detach it from projects in the web app too.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    config = load_config(state_dir)
    print(f"Hub: {config.hub_url}")
    print(f"Device: {config.device_name} ({config.device_id})")
    secret_present = load_device_secret(state_dir, str(config.device_id)) is not None
    print(f"Device secret present: {secret_present}")
    adapters = build_adapters(include_fake=False)
    for name, adapter in adapters.items():
        status = adapter.discover(None)
        print(f"Harness {name}: available={status.available} version={status.version or '-'} ({status.detail})")
    for agent in config.agents:
        adapter = adapters.get(agent.harness)
        ok = adapter is not None and adapter.discover(agent.harness_bin).available
        print(f"Agent {agent.handle}: harness_available={ok} repo={agent.default_repo}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    config = load_config(state_dir)
    if not config.device_id:
        print("This machine is not paired. Run `collab-bridge pair` first.", file=sys.stderr)
        return 1
    secret = load_device_secret(state_dir, config.device_id)
    if not secret:
        print("The device secret is missing. Pair this machine again.", file=sys.stderr)
        return 1
    state = BridgeState(state_dir / "bridge.sqlite3")
    adapters = build_adapters(include_fake=args.fake_harness)
    client = BridgeClient(config, config.device_id, secret, state, adapters)
    try:
        asyncio.run(client.run_forever())
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        state.close()
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    try:
        config = load_config(state_dir)
    except ConfigError:
        print("Nothing to forget.")
        return 0
    if config.device_id:
        delete_device_secret(state_dir, config.device_id)
    (state_dir / "config.json").unlink(missing_ok=True)
    print("Removed the local secret and configuration. Revoke the device in the web app as well.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collab-bridge", description="Connect local AI agents to the hub.")
    parser.add_argument("--state-dir", help="override the state directory (default: ~/.collab-bridge)")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    pair = sub.add_parser("pair", help="pair this machine using a one-time code from the web app")
    pair.add_argument("--hub", required=True)
    pair.add_argument("--code", required=True)
    pair.add_argument("--name", required=True)
    pair.set_defaults(func=cmd_pair)

    agent = sub.add_parser("agent", help="manage the agents this machine offers")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    add = agent_sub.add_parser("add")
    add.add_argument("--handle", required=True)
    add.add_argument("--display", required=True)
    add.add_argument("--harness", required=True, choices=["claude_code", "codex"])
    add.add_argument("--repo", action="append", required=True, help="approved repository root (repeatable)")
    add.add_argument("--default-repo")
    add.add_argument("--description")
    add.add_argument("--harness-bin", help="explicit path to the harness executable")
    add.set_defaults(func=cmd_agent_add)
    agent_sub.add_parser("list").set_defaults(func=cmd_agent_list)
    remove = agent_sub.add_parser("remove")
    remove.add_argument("--handle", required=True)
    remove.set_defaults(func=cmd_agent_remove)

    sub.add_parser("status", help="show pairing, harness and agent status").set_defaults(func=cmd_status)
    run = sub.add_parser("run", help="connect to the hub and serve agent requests")
    run.add_argument("--fake-harness", action="store_true", help="also offer the deterministic test harness")
    run.set_defaults(func=cmd_run)
    sub.add_parser("forget", help="remove local secret and configuration").set_defaults(func=cmd_forget)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        return int(args.func(args))
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
