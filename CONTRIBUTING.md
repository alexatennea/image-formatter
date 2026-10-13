# Contributing

## Commit messages

Every commit on `main` follows [Conventional Commits](https://www.conventionalcommits.org).
Releases are versioned and their notes written from these messages, so the
type you pick decides what gets released.

```
type(optional scope): short description in the imperative

Optional body: what changed and why.

Optional footers, e.g. BREAKING CHANGE: ... or Co-Authored-By: ...
```

| Type | Use it for | Release |
|------|-----------|---------|
| `feat` | Something new an operator can see or use | minor (1.2.0 -> 1.3.0) |
| `fix` | A bug fix | patch (1.2.0 -> 1.2.1) |
| `perf` | Faster or lighter, same behaviour | patch |
| `refactor` | Code change with no behaviour change | none |
| `docs` | README, comments, this file | none |
| `test` | Tests only | none |
| `build` | Dependencies, PyInstaller, the installer | none |
| `ci` | GitHub Actions workflows | none |
| `style` | Formatting only | none |
| `chore` | Anything else that isn't user-facing | none |
| `revert` | Undoing an earlier commit | none |

A breaking change, e.g. profiles saved by older versions no longer
loading, makes a major release (1.2.0 -> 2.0.0). Mark it with `!` after the
type (`feat!: ...`) or with a `BREAKING CHANGE: <what breaks>` footer.

The scope is optional, lowercase, and names the area touched, e.g.
`app`, `runner`, `extract`, `naming`, `profile`, `installer`.

Rules the checks enforce:

- the first line is `type: description` or `type(scope): description`,
  using a type from the table, in lowercase;
- the first line is at most 100 characters, with a blank line before any body;
- no `fixup!`/`squash!` commits left in a pull request.

Examples:

```
feat(app): remember the last folder used across restarts
fix(runner): remove the raw/ copy when a rename fails
build: pin onnxruntime 1.30.0
chore: tidy the README
```

### Where it's checked

- **As you commit**, by `.githooks/commit-msg`. Enable it once per clone:
  `git config core.hooksPath .githooks`
- **On every pull request**, by the *Commit messages* workflow. It checks
  every commit and the PR title, since a squash merge uses the title as
  the commit message.

Both run `tools/conventional_commits.py`, which `release.yml` also uses to
work out the next version.
