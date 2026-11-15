"""GitHub access for one run: an installation token limited to one repository, used for a single clone.

- The App private key is read from Secret Manager and signs a short-lived JWT (10 minutes).
- The JWT is exchanged for an installation token limited to the run's repository and to read access.
- The token is passed to git as an HTTP header for the clone only. It is not put in the clone URL, is not
  written to the git config, and is not logged. Git's credential helpers are disabled.
- Repository names and branches are validated before they reach git, so they cannot inject options.
"""

import base64
import re
import subprocess
import time

import httpx

GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}$")
REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
CLONE_TIMEOUT_SECONDS = 120


class GitHubError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def validate_repo(full_name: str) -> str:
    if not REPO_PATTERN.match(full_name) or full_name.endswith(".git"):
        raise GitHubError("invalid_repository")
    return full_name


def validate_ref(ref: str) -> str:
    if not REF_PATTERN.match(ref) or ".." in ref:
        raise GitHubError("invalid_ref")
    return ref


def make_app_jwt(app_id: str, private_key_pem: str, now: int) -> str:
    import jwt  # PyJWT with cryptography, RS256

    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key_pem, algorithm="RS256")


def installation_token(
    http: httpx.Client,
    *,
    app_id: str,
    installation_id: str,
    private_key_pem: str,
    repo_full_name: str,
    now: int | None = None,
) -> str:
    """Return a token that can read the contents of one repository, and nothing else."""
    repo_name = validate_repo(repo_full_name).split("/", 1)[1]
    app_jwt = make_app_jwt(app_id, private_key_pem, int(now if now is not None else time.time()))
    response = http.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"repositories": [repo_name], "permissions": {"contents": "read"}},
    )
    if response.status_code != 201:
        raise GitHubError("github_token_refused", f"HTTP {response.status_code}")
    token = response.json().get("token")
    if not isinstance(token, str) or not token:
        raise GitHubError("github_token_refused", "no token in response")
    return token


def clone_repository(
    *,
    repo_full_name: str,
    ref: str,
    token: str,
    destination: str,
    base_url: str = GITHUB_WEB,
    git: str = "git",
) -> bool:
    """Shallow-clone one branch. Returns False, without cloning, when the repository has no commits at all.

    Raises GitHubError with a code; never includes the token in messages.
    """
    validate_repo(repo_full_name)
    validate_ref(ref)
    url = f"{base_url.rstrip('/')}/{repo_full_name}.git"
    prefix = [git, "-c", "credential.helper=", "-c", "core.askPass="]
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        prefix += ["-c", f"http.extraHeader=Authorization: basic {basic}"]

    # An empty repository has no refs to clone. Check first, so it is reported as empty, not as a failed clone.
    probe = _git(prefix + ["ls-remote", url], destination, token)
    if probe.returncode != 0:
        raise GitHubError("clone_failed", _scrub(probe.stderr, token))
    if not probe.stdout.strip():
        return False

    result = _git(prefix + ["clone", "--depth", "50", "--single-branch", "--branch", ref, url, destination], destination, token)
    if result.returncode != 0:
        raise GitHubError("clone_failed", _scrub(result.stderr, token))
    return True


def refresh_repository(
    *,
    repo_full_name: str,
    ref: str,
    token: str,
    destination: str,
    base_url: str = GITHUB_WEB,
    git: str = "git",
) -> None:
    """Bring an existing clone up to the latest commit on its branch. Raises GitHubError with a code."""
    validate_repo(repo_full_name)
    validate_ref(ref)
    url = f"{base_url.rstrip('/')}/{repo_full_name}.git"
    prefix = [git, "-c", "credential.helper=", "-c", "core.askPass="]
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        prefix += ["-c", f"http.extraHeader=Authorization: basic {basic}"]
    fetched = _git([*prefix, "-C", destination, "fetch", "--depth", "50", url, ref], destination, token)
    if fetched.returncode != 0:
        raise GitHubError("fetch_failed", _scrub(fetched.stderr, token))
    reset = _git([git, "-C", destination, "reset", "--hard", "FETCH_HEAD"], destination, token)
    if reset.returncode != 0:
        raise GitHubError("fetch_failed", _scrub(reset.stderr, token))


def _git(command: list[str], home: str, token: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            env={"PATH": _path(), "GIT_TERMINAL_PROMPT": "0", "HOME": home},
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GitHubError("clone_timeout") from error
    except OSError as error:
        raise GitHubError("git_missing", type(error).__name__) from error


def _scrub(text: str, token: str) -> str:
    # git's own messages do not include the header, but scrub it anyway.
    return text.replace(token, "[redacted]")[-300:] if token else text[-300:]


def _path() -> str:
    import os

    return os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")


__all__ = [
    "GitHubError",
    "clone_repository",
    "installation_token",
    "make_app_jwt",
    "validate_ref",
    "validate_repo",
]
