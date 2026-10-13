import subprocess
import sys
from pathlib import Path

import pytest

# tools/ is a folder of standalone scripts, not a package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import conventional_commits as cc  # noqa: E402


@pytest.mark.parametrize("message", [
    "feat: remember the last folder used",
    "fix(runner): keep the raw copy when a rename fails",
    "chore!: drop Python 3.11",
    "ci: add release workflow\n\nLonger body.\n\nCo-Authored-By: Someone <a@b.c>",
    "Merge pull request #1 from x/y",
    'Revert "feat: something"',
    "feat: thing\n# Please enter the commit message\n# Lines starting with '#' are ignored",
])
def test_valid_messages(message):
    assert cc.check_message(message) is None


@pytest.mark.parametrize("message, reason_part", [
    ("Add a thing", "doesn't match"),
    ("feature: add a thing", "unknown type"),
    ("feat:add a thing", "doesn't match"),
    ("Feat: add a thing", "doesn't match"),
    ("feat(Runner): add", "doesn't match"),
    ("feat: " + "x" * 100, "characters"),
    ("feat: thing\nno blank line", "blank line"),
    ("", "empty"),
    ("# only a comment", "empty"),
])
def test_invalid_messages(message, reason_part):
    assert reason_part in cc.check_message(message)


def test_autosquash_commits_allowed_locally_but_not_in_ci():
    assert cc.check_message("fixup! feat: x", allow_autosquash=True) is None
    assert "fixup" in cc.check_message("fixup! feat: x")


def commits(*messages):
    return [cc.Commit(f"{i:040x}", m) for i, m in enumerate(messages)]


@pytest.mark.parametrize("messages, expected", [
    (["chore: tidy", "docs: readme", "ci: workflow"], (1, 2, 0)),
    (["fix: a", "chore: b"], (1, 2, 1)),
    (["perf: faster"], (1, 2, 1)),
    (["fix: a", "feat: b"], (1, 3, 0)),
    (["feat: a", "feat!: b"], (2, 0, 0)),
    (["fix: a\n\nBREAKING CHANGE: profiles from 1.x no longer load"], (2, 0, 0)),
    (["Add something (not conventional)"], (1, 2, 0)),
])
def test_next_version(messages, expected):
    assert cc.bump((1, 2, 0), cc.bump_level(commits(*messages))) == expected


def test_release_notes_group_by_type():
    notes = cc.release_notes(commits("feat(app): add A", "fix: fix B", "chore: C", "feat!: D"))
    assert "## Breaking changes\n\n- D (" in notes
    assert "## Features\n\n- **app:** add A (" in notes
    assert "- D (" in notes.split("## Features")[1]
    assert "## Fixes\n\n- fix B (" in notes
    assert "C" not in notes.replace("Breaking changes", "")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_next_release_against_a_real_repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "Old style message")
    _git(tmp_path, "tag", "-a", "v1.2.0", "-m", "v1.2.0")
    _git(tmp_path, "tag", "v1.10.0-not-a-release")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "fix: one")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "feat: two\n\nwith | odd \x1f chars")
    monkeypatch.chdir(tmp_path)

    out = tmp_path / "out.txt"
    notes = tmp_path / "notes.md"
    assert cc.main(["next-release", "--github-output", str(out), "--notes", str(notes)]) == 0
    assert out.read_text() == "release=true\nversion=1.3.0\ntag=v1.3.0\n"
    assert "- two (" in notes.read_text() and "- one (" in notes.read_text()

    _git(tmp_path, "tag", "v1.3.0")
    out.unlink()
    assert cc.main(["next-release", "--github-output", str(out)]) == 0
    assert out.read_text() == "release=false\nversion=\ntag=\n"

    assert cc.main(["check-range", "v1.2.0..HEAD"]) == 0
    assert cc.main(["check-range", "HEAD"]) == 1  # includes the old-style first commit
