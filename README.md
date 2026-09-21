# Image Batch Renamer

Local desktop app that renames folders of photos using text printed on the
images, driven by a reusable template. No network access; OCR runs fully
on-device via RapidOCR (ONNX). See the build spec for full rationale.

## Development

Requires Python 3.11 or 3.12 (PyInstaller and the ONNX runtime wheel tend
to lag the newest CPython release).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m image_renamer.app
```

## Tests

Unit tests need no display and no OCR install:

```bash
python -m pytest tests/
```

## Packaging

Build on the target OS -- PyInstaller does not cross-compile. The exact
commands the CI workflows run are the source of truth (see below); roughly:

```bash
# Windows -- no --onefile (as of the fix below), matching macOS.
pyinstaller --windowed --name ImageRenamer \
  --icon assets/icon.ico \
  --collect-data rapidocr_onnxruntime \
  -p . \
  image_renamer/app.py

# macOS -- also no --onefile: PyInstaller warns that combining --onefile
# with --windowed on macOS "clashes with macOS's security" and is slated
# to become a hard error. --windowed alone still produces a proper .app
# bundle either way.
pyinstaller --windowed --name ImageRenamer \
  --icon assets/icon.icns \
  --collect-data rapidocr_onnxruntime \
  -p . \
  image_renamer/app.py
```

Both platforms build in `--onedir` mode (a plain folder of files run in
place), not `--onefile` (which bundles everything into one self-extracting
binary that unpacks itself to a fresh temp folder on *every launch*).
Windows originally used `--onefile` for the convenience of a single
portable `.exe`, but a real operator machine hit OCR output degrading into
near-random characters (digits, symbols, stray CJK glyphs) that pointed to
the ~100MB of bundled ONNX model weights getting corrupted during that
per-launch temp extraction -- legacy Windows `MAX_PATH` truncation and
antivirus interference during unpacking are the likely culprits, though it
wasn't practical to pin down the exact mechanism on a machine we don't
have direct access to. `--onedir` removes runtime unpacking entirely, so
this whole class of failure is no longer possible regardless of the exact
cause. The tradeoff is operators now get a folder to unzip rather than a
single `.exe` -- documented below.

`--collect-data` is required or the bundled OCR models are omitted and the
app fails at first extraction with a missing-file error. Windows takes
`assets/icon.ico`, macOS takes `assets/icon.icns` -- same artwork
(`assets/icon_source.png`), exported in each platform's own icon format;
PyInstaller ignores `--icon` if you pass the wrong format for the target
OS. `assets/icon.icns` was built with Apple's own `iconutil` from a set of
rendered PNGs at each required size (16 up to 1024px, `@2x` retina
variants included) -- see git history for the exact steps if it ever needs
regenerating.

`image_renamer/app.py` imports its sibling modules with absolute imports
(`from image_renamer import extract, ...`), not relative ones (`from . import
...`) -- PyInstaller runs the frozen entry script without any package
context, so a relative import there fails at startup with `ImportError:
attempted relative import with no known parent package`. This only shows up
in the frozen build, not in normal `python -m image_renamer.app` runs, so
it's easy to miss locally.

### CI builds

`.github/workflows/build-windows.yml` and `.github/workflows/build-macos.yml`
each run tests then build on their native OS (PyInstaller doesn't
cross-compile).

- **Every push to `main`**, and **manual run** (Actions tab -> pick the
  workflow -> Run workflow): the build is attached to that run as a
  downloadable artifact (`ImageRenamer-windows`, a folder containing
  `ImageRenamer.exe` alongside its supporting files -- run the `.exe` from
  inside that folder, don't move it out on its own; `ImageRenamer-mac`,
  containing the `ImageRenamer.app` bundle). GitHub always wraps a
  workflow artifact in its own zip on download -- extract that once and
  the folder/`.app` is right there; no second zip to unpack. Only the 3
  most recent artifacts of each are kept; each workflow run prunes older
  ones under the same name.
- **Release**: push a tag matching `v*.*.*` (e.g. `git tag v1.0.0 && git
  push origin v1.0.0`) and both workflows also attach a build to a GitHub
  Release for that tag -- both zipped, since a Release asset has to be a
  single file and neither the Windows folder nor the macOS `.app` is one
  -- so others can download it from the Releases page without needing
  repo access to Actions. Release assets are not pruned -- only the plain
  workflow-run artifacts are.

The Windows `.exe` is code-signed via Azure Trusted Signing (see below) --
SmartScreen reputation still builds up gradually after release, so early
downloads may briefly show a warning regardless. The macOS build is not
signed/notarized, so it will trigger a Gatekeeper warning ("unidentified
developer"); right-click the app -> Open the first time to bypass it.

### Windows code signing

Signed via [Azure Trusted Signing](https://azure.microsoft.com/en-us/products/trusted-signing),
using OIDC federation -- no certificate or client secret is stored in the
repo. `build-windows.yml`'s job runs under the `release` GitHub
environment and authenticates to Azure as the `github-image-renamer-signing`
Entra ID app, which is granted the "Artifact Signing Certificate Profile
Signer" role scoped only to the `ennea` certificate profile under the
`ennealimited` Trusted Signing account (resource group `software-signing`,
West Europe). The environment (rather than a branch/tag-scoped federated
credential) is what lets one credential cover every trigger -- push to
main, any version tag, or a manual run -- since Azure federated
credentials require an exact subject match and tag names vary per release.

The certificate profile identifies the *publisher* (Ennea Limited), not
this specific app, so it's shared across whatever else gets signed this
way -- Trusted Signing's Basic tier only allows one certificate profile
per account, so this is also a hard constraint, not just a tidiness
choice.

Repo variables `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_SUBSCRIPTION_ID`
hold the (non-secret) identifiers the workflow needs; nothing else is
required to rotate or reproduce this setup beyond those three values plus
the certificate profile name and endpoint already hardcoded in the
workflow.

## Module boundaries

| Module | Responsibility | Imports GUI? |
| --- | --- | --- |
| `image_renamer/profile.py` | Profile dataclasses, load, save, version check | No |
| `image_renamer/geometry.py` | Coordinate conversion, padding, layout guard | No |
| `image_renamer/extract.py` | Crop, preprocess, OCR call, postprocess, validate | No |
| `image_renamer/naming.py` | Template resolution, sanitisation, collisions | No |
| `image_renamer/runner.py` | Folder scan, hashing, orchestration, manifest, undo | No |
| `image_renamer/app.py` | Tkinter windows, canvas, dialogs, wiring | Yes |

`runner.preview(folder, profile)` and `runner.apply(rows, folder, profile)`
are the only entry points the GUI calls into batch logic, plus
`runner.undo_last_run(folder)`.
