"""Device secret storage.

Preferred: the operating system credential store, via the `keyring` package (Windows Credential
Manager, macOS Keychain, Secret Service on Linux). Fallback: a file in the state directory with
owner-only permissions where the platform supports them. The secret is never written to config.json,
logs, or the hub.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

SERVICE_NAME = "collab-bridge"


def _keyring():  # type: ignore[no-untyped-def]
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return None
    return keyring


def _fallback_path(state_dir: Path, device_id: str) -> Path:
    return state_dir / f"device-{device_id}.secret"


def store_device_secret(state_dir: Path, device_id: str, secret: str) -> str:
    """Store the secret. Returns where it went ("os-keyring" or "file") for the user to see."""
    backend = _keyring()
    if backend is not None:
        try:
            backend.set_password(SERVICE_NAME, device_id, secret)
            return "os-keyring"
        except Exception:  # keyring backends raise various errors; fall back rather than fail pairing
            pass
    state_dir.mkdir(parents=True, exist_ok=True)
    path = _fallback_path(state_dir, device_id)
    path.write_text(secret, encoding="utf-8")
    # Windows ignores POSIX modes; the file lives in the user's profile directory there.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return "file"


def load_device_secret(state_dir: Path, device_id: str) -> str | None:
    backend = _keyring()
    if backend is not None:
        try:
            value = backend.get_password(SERVICE_NAME, device_id)
            if value:
                return value
        except Exception:
            pass
    path = _fallback_path(state_dir, device_id)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None


def delete_device_secret(state_dir: Path, device_id: str) -> None:
    backend = _keyring()
    if backend is not None:
        with contextlib.suppress(Exception):  # the credential may already be gone
            backend.delete_password(SERVICE_NAME, device_id)
    path = _fallback_path(state_dir, device_id)
    if path.exists():
        path.unlink()
