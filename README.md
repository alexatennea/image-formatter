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

Build on the target OS -- PyInstaller does not cross-compile.

```bash
pyinstaller --onefile --windowed --name ImageRenamer \
  --collect-data rapidocr_onnxruntime \
  -p . \
  image_renamer/app.py
```

`--collect-data` is required or the bundled OCR models are omitted and the
app fails at first extraction with a missing-file error. `--windowed`
suppresses the console window on Windows.

### CI builds

`.github/workflows/build-windows.yml` and `.github/workflows/build-macos.yml`
each run tests then build on their native OS (PyInstaller doesn't
cross-compile).

- **Manual run**: Actions tab -> pick the workflow -> Run workflow. The
  build is attached to that run as a downloadable artifact
  (`ImageRenamer-windows`, containing `ImageRenamer.exe`; `ImageRenamer-mac`,
  containing `ImageRenamer.app`). GitHub always wraps a workflow artifact in
  its own zip on download -- extract that once and the `.exe`/`.app` is
  right there; no second zip to unpack.
- **Release**: push a tag matching `v*.*.*` (e.g. `git tag v1.0.0 && git
  push origin v1.0.0`) and both workflows also attach a build to a GitHub
  Release for that tag (the Windows `.exe` directly, the macOS `.app`
  zipped since a Release asset has to be a single file), so others can
  download it from the Releases page without needing repo access to
  Actions.

Neither build is code-signed, so first launch will trigger a Windows
SmartScreen warning or a macOS Gatekeeper warning ("unidentified
developer"); this is expected without a paid signing certificate. On
macOS, right-click the app -> Open the first time to bypass it.

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
