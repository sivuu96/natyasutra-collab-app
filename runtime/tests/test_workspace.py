"""The session summary: the file list, what a turn changed, and the diff."""

import os
import subprocess

import pytest

from collab_runtime import workspace


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _git_repo(root: str) -> None:
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "t@example.invalid"], check=True)
    _write(os.path.join(root, "notes.md"), "one\n")
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-q", "-m", "start"], check=True)


def test_scan_lists_files_and_leaves_out_git_and_rebuildable_folders(tmp_path) -> None:
    root = str(tmp_path)
    _git_repo(root)
    _write(os.path.join(root, "node_modules", "pkg", "index.js"), "x")
    _write(os.path.join(root, "src", "main.py"), "print(1)")

    files = workspace.scan(root)

    assert "notes.md" in files and "src/main.py" in files
    assert not [p for p in files if p.startswith(".git/") or "node_modules" in p]


def test_changes_report_added_changed_and_removed_files_only(tmp_path) -> None:
    root = str(tmp_path)
    _write(os.path.join(root, "keep.txt"), "same")
    _write(os.path.join(root, "edit.txt"), "before")
    _write(os.path.join(root, "gone.txt"), "bye")
    before = workspace.scan(root)

    _write(os.path.join(root, "edit.txt"), "after")
    _write(os.path.join(root, "new.txt"), "hello")
    os.remove(os.path.join(root, "gone.txt"))
    after = workspace.scan(root)

    result = {(c["path"], c["change"]) for c in workspace.changes(before, after)}
    assert result == {("edit.txt", "changed"), ("new.txt", "added"), ("gone.txt", "removed")}


def test_a_rewrite_with_the_same_content_is_not_a_change(tmp_path) -> None:
    root = str(tmp_path)
    _write(os.path.join(root, "a.txt"), "same")
    before = workspace.scan(root)
    _write(os.path.join(root, "a.txt"), "same")
    assert workspace.changes(before, workspace.scan(root)) == []


def test_diff_shows_tracked_edits_and_new_files(tmp_path) -> None:
    root = str(tmp_path)
    _git_repo(root)
    _write(os.path.join(root, "notes.md"), "one\ntwo\n")
    _write(os.path.join(root, "fresh.md"), "brand new\n")

    text, truncated = workspace.diff(root)

    assert truncated is False
    assert "+two" in text
    assert "fresh.md" in text and "+brand new" in text


def test_a_folder_that_is_not_a_git_repository_has_no_diff(tmp_path) -> None:
    assert workspace.diff(str(tmp_path)) == (None, False)


def test_a_long_diff_is_cut_to_the_limit_and_marked(tmp_path, monkeypatch) -> None:
    root = str(tmp_path)
    _git_repo(root)
    _write(os.path.join(root, "notes.md"), "line\n" * 500)
    monkeypatch.setattr(workspace, "MAX_DIFF_CHARS", 200)

    text, truncated = workspace.diff(root)

    assert truncated is True and len(text) == 200


@pytest.mark.parametrize("key", ["file_count", "total_bytes", "files", "changes", "diff", "diff_truncated"])
def test_the_summary_has_every_field_the_hub_stores(tmp_path, key: str) -> None:
    root = str(tmp_path)
    _git_repo(root)
    before = workspace.scan(root)
    assert key in workspace.summary(root, before)


def test_files_git_ignores_are_left_out_of_the_tree(tmp_path) -> None:
    root = str(tmp_path)
    _git_repo(root)
    _write(os.path.join(root, ".gitignore"), "*.env\nbuild/\n")
    _write(os.path.join(root, ".env"), "SECRET=1")
    _write(os.path.join(root, "local.env"), "SECRET=2")
    _write(os.path.join(root, "build", "out.bin"), "x")
    _write(os.path.join(root, "app.py"), "print(1)")

    files = workspace.scan(root)

    assert "app.py" in files and ".gitignore" in files
    assert ".env" not in files and "local.env" not in files
    assert not [p for p in files if p.startswith("build/")]
