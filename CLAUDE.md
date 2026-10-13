# Project notes for Claude

## Commits and pull requests

- Every commit message must be a Conventional Commit:
  `type(optional scope): description`, with type one of feat, fix, perf,
  refactor, docs, style, test, build, ci, chore, revert. Releases are
  versioned automatically from these (feat -> minor, fix/perf -> patch,
  `!` or a `BREAKING CHANGE:` footer -> major), so pick the type by what
  an operator would notice. CONTRIBUTING.md has the full rules.
- Pull request titles follow the same format: a squash merge uses the
  title as the commit message on main.
- Check messages before pushing:
  `python tools/conventional_commits.py check-range origin/main..HEAD`

## Tests

`python -m pytest tests/ -q` -- no display or OCR install needed.
