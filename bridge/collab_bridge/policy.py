"""Local policy: what this bridge will do on this machine, regardless of what the hub asks.

The hub never sends a path or a shell command. It sends an agent handle and a prompt. The working
directory comes from local configuration and is re-checked here on every run.
"""

from __future__ import annotations

from pathlib import Path

from collab_bridge.config import AgentConfig

# Tools that only read. They run without asking. Anything else needs an explicit human approval.
READ_ONLY_TOOLS = frozenset({"Read", "Glob", "Grep"})


class PolicyError(Exception):
    """Raised when a request would break a local rule. The invocation is rejected, not run."""


def resolve_working_directory(agent: AgentConfig) -> Path:
    """Return the agent's working directory, or raise if it is missing or escapes the approved roots.

    Symlinks are resolved before the containment check, so a link inside a root that points outside
    it is refused.
    """
    if not agent.default_repo:
        raise PolicyError("this agent has no default repository configured")
    try:
        candidate = Path(agent.default_repo).expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise PolicyError("the agent's repository path is missing") from error
    for root in agent.repo_roots:
        try:
            root_resolved = Path(root).expanduser().resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        if candidate == root_resolved or root_resolved in candidate.parents:
            if not candidate.is_dir():
                raise PolicyError("the agent's repository path is not a directory")
            return candidate
    raise PolicyError("working directory is outside the approved repository roots")


def tool_base_name(tool: str) -> str:
    """'Bash(git commit:*)' -> 'Bash'. Permission rules are reduced to the tool they govern."""
    return tool.split("(", 1)[0].strip()


def tools_needing_approval(tools: list[str]) -> list[str]:
    return sorted({tool_base_name(t) for t in tools if tool_base_name(t) not in READ_ONLY_TOOLS})


def allowed_tools_for(approved: list[str]) -> list[str]:
    """Read-only tools plus whatever a human has approved for this invocation."""
    names = set(READ_ONLY_TOOLS)
    names.update(tool_base_name(t) for t in approved)
    return sorted(names)
