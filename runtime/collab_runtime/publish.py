"""Commit the agent's changes and push them to the agent's own branch, from the clone it worked in.

Only runs whose agent may write reach this module. Git runs with hooks, fsmonitor and credential helpers switched
off. The write token is passed as a header for the push command only, and is never written to the clone.
"""

import base64

from collab_runtime.github import GITHUB_WEB, _git, _scrub

PROTECTED_REFS = frozenset({"main", "master"})
# Files that must never be committed, whatever the agent wrote into them.
FORBIDDEN_NAMES = (".env", ".envrc", "id_rsa", "id_ed25519")
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12")

WRITE_INSTRUCTIONS = (
    "You may change files in the repository to do what the user asked. Make only the changes the user asked for. "
    "Do not commit and do not push: the runtime commits your changes and pushes them to this branch when you finish. "
    "Do not create, change or delete files under .git."
)

GIT_SAFETY = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "credential.helper=", "-c", "core.askPass="]
AUTHOR = ["-c", "user.name=NatyaSutra agent", "-c", "user.email=agent@natyasutra.invalid"]


class PublishError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def _git_in(folder: str, args: list[str], token: str = "", extra: list[str] | None = None):  # type: ignore[no-untyped-def]
    return _git(["git", *GIT_SAFETY, *(extra or []), "-C", folder, *args], folder, token)


def has_changes(folder: str) -> bool:
    result = _git_in(folder, ["status", "--porcelain"])
    if result.returncode != 0:
        raise PublishError("status_failed")
    return bool(result.stdout.strip())


def _check_names(folder: str) -> None:
    names = _git_in(folder, ["diff", "--cached", "--name-only"]).stdout.splitlines()
    for name in names:
        base = name.rsplit("/", 1)[-1]
        if base in FORBIDDEN_NAMES or base.endswith(FORBIDDEN_SUFFIXES):
            raise PublishError("forbidden_file", f"{base} is not committed by agents")
    # A symbolic link would point git at something outside the repository, so it is refused.
    raw = _git_in(folder, ["diff", "--cached", "--raw"]).stdout.splitlines()
    if any(len(line.split()) > 1 and line.split()[1] == "120000" for line in raw):
        raise PublishError("symlink", "symbolic links are not committed by agents")


def commit_and_push(
    folder: str,
    *,
    repo_full_name: str,
    ref: str,
    token: str,
    message: str,
    base_url: str = GITHUB_WEB,
) -> str | None:
    """Commit every change in the clone and push it to `ref`. Returns the new commit, or None if nothing changed.

    Raises PublishError. The push is a normal fast-forward push: a branch that moved is reported, not overwritten.
    """
    if ref in PROTECTED_REFS:
        raise PublishError("protected_branch", f"{ref} is protected")
    if not has_changes(folder):
        return None

    if _git_in(folder, ["add", "-A"]).returncode != 0:
        raise PublishError("stage_failed")
    _check_names(folder)
    commit = _git_in(folder, ["commit", "--no-verify", "-m", message], extra=AUTHOR)
    if commit.returncode != 0:
        raise PublishError("commit_failed")
    sha = _git_in(folder, ["rev-parse", "HEAD"]).stdout.strip()

    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    url = f"{base_url.rstrip('/')}/{repo_full_name}.git"
    push = _git_in(
        folder,
        ["push", url, f"HEAD:refs/heads/{ref}"],
        token=token,
        extra=["-c", f"http.extraHeader=Authorization: basic {basic}"],
    )
    if push.returncode != 0:
        raise PublishError("push_rejected", _scrub(push.stderr, token)[:200])
    return sha


__all__ = ["PROTECTED_REFS", "WRITE_INSTRUCTIONS", "PublishError", "commit_and_push", "has_changes"]
