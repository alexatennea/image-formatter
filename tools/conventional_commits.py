"""Conventional Commits: message checks, the next release version, and
release notes. Standard library only, so it runs in CI, in the
commit-msg hook and in tests with nothing extra installed.

    python tools/conventional_commits.py check-file .git/COMMIT_EDITMSG
    python tools/conventional_commits.py check-range origin/main..HEAD
    python tools/conventional_commits.py check-title "feat: add thing"
    python tools/conventional_commits.py next-release [--notes PATH] [--github-output PATH]

Version bumps, counted from the latest vX.Y.Z tag reachable from HEAD:
  a breaking change ("type!:" or a "BREAKING CHANGE:" footer) -> major
  feat                                                        -> minor
  fix, perf                                                   -> patch
  anything else (chore, docs, ci, refactor, ...)              -> no release
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

TYPES = ("feat", "fix", "perf", "refactor", "docs", "style", "test", "build", "ci", "chore", "revert")
HEADER_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[a-z0-9][a-z0-9._/-]*)\))?(?P<breaking>!)?: (?P<description>\S.*)$"
)
BREAKING_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE: ", re.MULTILINE)
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
MAX_HEADER_LENGTH = 100

# Written by git itself, not by a person: merge commits, `git revert`'s
# default message, and fixup!/squash! commits that `rebase --autosquash`
# folds away before they reach main.
_GIT_GENERATED = ("Merge ", 'Revert "')
_AUTOSQUASH = ("fixup! ", "squash! ", "amend! ")

MAJOR, MINOR, PATCH = 3, 2, 1


@dataclass
class Commit:
    sha: str
    message: str

    @property
    def header(self) -> str:
        return self.message.strip().splitlines()[0] if self.message.strip() else ""


@dataclass
class Parsed:
    type: str
    scope: str | None
    breaking: bool
    description: str


def parse(message: str) -> Parsed | None:
    lines = message.strip().splitlines()
    if not lines:
        return None
    match = HEADER_RE.match(lines[0])
    if not match or match["type"] not in TYPES:
        return None
    breaking = bool(match["breaking"]) or bool(BREAKING_FOOTER_RE.search(message))
    return Parsed(match["type"], match["scope"], breaking, match["description"].strip())


def check_message(message: str, *, allow_autosquash: bool = False) -> str | None:
    """Returns why the message is not a valid Conventional Commit, or None."""
    # Drop the comment lines git puts in the editor template.
    message = "\n".join(l for l in message.splitlines() if not l.startswith("#")).strip()
    if not message:
        return "the commit message is empty"
    header = message.splitlines()[0]
    if header.startswith(_GIT_GENERATED):
        return None
    if header.startswith(_AUTOSQUASH):
        return None if allow_autosquash else f"{header!r} is a fixup/squash commit; squash it before merging"
    if len(header) > MAX_HEADER_LENGTH:
        return f"the first line is {len(header)} characters; keep it to {MAX_HEADER_LENGTH}"
    match = HEADER_RE.match(header)
    if not match:
        return (
            f"{header!r} doesn't match 'type(optional scope): description', "
            f"e.g. 'feat: add output crop' or 'fix(runner): keep backup on failure'"
        )
    if match["type"] not in TYPES:
        return f"unknown type {match['type']!r}; use one of: {', '.join(TYPES)}"
    lines = message.splitlines()
    if len(lines) > 1 and lines[1].strip():
        return "leave a blank line between the first line and the body"
    return None


def bump_level(commits: list[Commit]) -> int:
    level = 0
    for commit in commits:
        parsed = parse(commit.message)
        if parsed is None:
            continue
        if parsed.breaking:
            level = max(level, MAJOR)
        elif parsed.type == "feat":
            level = max(level, MINOR)
        elif parsed.type in ("fix", "perf"):
            level = max(level, PATCH)
    return level


def bump(version: tuple[int, int, int], level: int) -> tuple[int, int, int]:
    major, minor, patch = version
    if level == MAJOR:
        return (major + 1, 0, 0)
    if level == MINOR:
        return (major, minor + 1, 0)
    if level == PATCH:
        return (major, minor, patch + 1)
    return version


def release_notes(commits: list[Commit]) -> str:
    sections = {"breaking": [], "feat": [], "fix": [], "perf": []}
    for commit in commits:
        parsed = parse(commit.message)
        if parsed is None:
            continue
        scope = f"**{parsed.scope}:** " if parsed.scope else ""
        line = f"- {scope}{parsed.description} ({commit.sha[:7]})"
        if parsed.breaking:
            sections["breaking"].append(line)
        if parsed.type in sections:
            sections[parsed.type].append(line)
    titles = {"breaking": "Breaking changes", "feat": "Features", "fix": "Fixes", "perf": "Performance"}
    parts = [f"## {titles[key]}\n\n" + "\n".join(lines) for key, lines in sections.items() if lines]
    return "\n\n".join(parts) + "\n"


# -- git ---------------------------------------------------------------------

def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def latest_tag() -> str | None:
    """The newest vX.Y.Z tag reachable from HEAD, by version number."""
    tags = _git("tag", "--merged", "HEAD", "--list", "v*").split()
    versions = [(tuple(int(p) for p in m.groups()), t) for t in tags if (m := TAG_RE.match(t))]
    return max(versions)[1] if versions else None


def commits_in(rev_range: str) -> list[Commit]:
    # NUL-separated records: commit messages can contain any other character.
    out = _git("log", "--format=%H%x1f%B%x00", "--reverse", rev_range)
    commits = []
    for record in out.split("\x00"):
        record = record.strip("\n")
        if record:
            sha, _, message = record.partition("\x1f")
            commits.append(Commit(sha.strip(), message))
    return commits


# -- CLI ---------------------------------------------------------------------

def _report(failures: list[tuple[str, str]]) -> int:
    for where, reason in failures:
        print(f"::error::{where}: {reason}" if where else f"error: {reason}", file=sys.stderr)
    if failures:
        print(
            "\nCommit messages must follow Conventional Commits "
            "(https://www.conventionalcommits.org), e.g.\n"
            "  feat: remember the last folder used\n"
            "  fix(runner): keep the raw/ copy when a rename fails\n"
            "  chore: bump pinned dependencies\n"
            f"Types: {', '.join(TYPES)}. See CONTRIBUTING.md.",
            file=sys.stderr,
        )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check-file", help="check one message file (the commit-msg hook)")
    p.add_argument("path")
    p = sub.add_parser("check-range", help="check every commit in a git revision range")
    p.add_argument("rev_range")
    p = sub.add_parser("check-title", help="check a pull request title (it becomes the squash-merge message)")
    p.add_argument("title")
    p = sub.add_parser("next-release", help="work out the next version from commits since the last tag")
    p.add_argument("--notes", help="write release notes to this file")
    p.add_argument("--github-output", help="append release/version/tag outputs to this file")
    args = parser.parse_args(argv)

    if args.command == "check-file":
        with open(args.path, encoding="utf-8") as fh:
            reason = check_message(fh.read(), allow_autosquash=True)
        return _report([("", reason)] if reason else [])

    if args.command == "check-range":
        failures = []
        for commit in commits_in(args.rev_range):
            reason = check_message(commit.message)
            if reason:
                failures.append((f"commit {commit.sha[:7]}", reason))
        return _report(failures)

    if args.command == "check-title":
        reason = check_message(args.title)
        return _report([("PR title", reason)] if reason else [])

    tag = latest_tag()
    current = tuple(int(p) for p in TAG_RE.match(tag).groups()) if tag else (0, 0, 0)
    commits = commits_in(f"{tag}..HEAD" if tag else "HEAD")
    for commit in commits:
        if check_message(commit.message):
            print(f"::warning::commit {commit.sha[:7]} isn't a Conventional Commit, so it can't affect the version: {commit.header!r}")
    level = bump_level(commits)
    new = bump(current, level)
    release = level > 0
    version = ".".join(map(str, new))
    print(f"latest tag: {tag or '(none)'}; {len(commits)} commit(s) since; "
          + (f"next release: v{version}" if release else "no release-worthy commits (feat/fix/perf/breaking)"))
    if args.notes:
        with open(args.notes, "w", encoding="utf-8") as fh:
            fh.write(release_notes(commits) if release else "")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"release={'true' if release else 'false'}\n")
            fh.write(f"version={version if release else ''}\n")
            fh.write(f"tag={'v' + version if release else ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
