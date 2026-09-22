"""Folder scan, hashing, orchestration, manifest and undo.

The GUI calls only `preview`, `apply` and `undo_last_run`. Everything here
is importable and testable without a display.
"""
from __future__ import annotations

import csv
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import extract, geometry, naming
from .profile import Profile

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
ORIGINALS_SUBFOLDER = "_originals"

STATUS_LAYOUT_MISMATCH = "LAYOUT_MISMATCH"
STATUS_UNREADABLE = "UNREADABLE"
STATUS_EMPTY_FIELD = "EMPTY_FIELD"
STATUS_VALIDATION_FAILED = "VALIDATION_FAILED"
STATUS_COLLISION_UNRESOLVED = "COLLISION_UNRESOLVED"
STATUS_OK = "OK"
STATUS_ALREADY_PROCESSED = "ALREADY_PROCESSED"
STATUS_UNDONE = "UNDONE"
"""Manifest-only status: the row was applied, then reverted by undo."""


DEFAULT_CHOICE_OPTIONS = ["intact", "split"]


@dataclass
class Row:
    original_path: str
    original_name: str
    sha256: str = ""
    extracted: dict[str, str] = field(default_factory=dict)
    proposed_name: str = ""
    status: str = STATUS_OK
    detail: str = ""
    index: int = 0


@dataclass
class Report:
    renamed: list[Row] = field(default_factory=list)
    failed: list[tuple[Row, str]] = field(default_factory=list)
    manifest_path: str = ""


def _list_image_files(folder: str) -> list[str]:
    paths = []
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        if os.path.splitext(name)[1].lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            with open(full, "rb") as fh:
                header = fh.read(12)
            if not header:
                continue
        except OSError:
            continue
        paths.append(full)
    return paths


def _previously_ok_hashes(folder: str) -> set[str]:
    hashes: set[str] = set()
    for name in os.listdir(folder):
        if name.startswith("_rename_manifest_") and name.endswith(".csv"):
            try:
                with open(os.path.join(folder, name), newline="", encoding="utf-8") as fh:
                    for row in csv.DictReader(fh):
                        if row.get("status") == STATUS_OK and row.get("sha256"):
                            hashes.add(row["sha256"])
            except OSError:
                continue
    return hashes


def preview(
    folder: str,
    profile: Profile,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[Row]:
    files = _list_image_files(folder)
    already_ok = _previously_ok_hashes(folder)
    existing_names = {os.path.basename(p) for p in os.listdir(folder)} if os.path.isdir(folder) else set()

    rows: list[Row] = []
    taken_names: set[str] = set(existing_names)

    total = len(files)
    for index, path in enumerate(files, start=1):
        if cancel_check and cancel_check():
            break
        if progress:
            progress(index, total)

        name = os.path.basename(path)
        row = Row(original_path=path, original_name=name, index=index)

        try:
            file_hash = naming.sha256_of_file(path)
        except OSError:
            row.status = STATUS_UNREADABLE
            row.detail = "Could not read file"
            rows.append(row)
            continue
        row.sha256 = file_hash

        if file_hash in already_ok:
            row.status = STATUS_ALREADY_PROCESSED
            rows.append(row)
            continue

        try:
            image = extract.load_image(path)
        except Exception as exc:  # noqa: BLE001 - any decode failure is UNREADABLE
            row.status = STATUS_UNREADABLE
            row.detail = str(exc)
            rows.append(row)
            continue

        width, height = image.size
        layout = geometry.check_layout(
            width,
            height,
            profile.reference_image.aspect_ratio,
            profile.layout_guard.aspect_tolerance,
            profile.layout_guard.enabled,
        )
        if not layout.ok:
            row.status = STATUS_LAYOUT_MISMATCH
            row.detail = (
                f"expected aspect ratio {layout.expected_ratio:.4f}, "
                f"got {layout.actual_ratio:.4f}"
            )
            rows.append(row)
            continue

        worst_status = STATUS_OK
        for f in profile.fields:
            if f.type == "choice":
                # Not OCR'd: the operator sets this per-row in the Run tab
                # (e.g. split vs intact), since it's a judgement call OCR
                # and pixel heuristics can't make reliably. Seed it with
                # the field's default so every row starts as one value and
                # the operator only has to correct the exceptions.
                choices = f.options.get("choices") or DEFAULT_CHOICE_OPTIONS
                row.extracted[f.name] = f.options.get("default", choices[0])
                continue

            crop = extract.crop_field(image, f.rect)
            preprocessed = extract.preprocess_for_ocr(crop)
            raw_text = extract.ocr_engine(preprocessed)
            result = extract.extract_and_validate_field(
                f.name, f.type, raw_text, f.options, f.required
            )
            row.extracted[f.name] = result.value
            if result.status != STATUS_OK and worst_status == STATUS_OK:
                worst_status = result.status

        if worst_status != STATUS_OK:
            row.status = worst_status
            rows.append(row)
            continue

        stem = os.path.splitext(name)[0]
        ext = os.path.splitext(name)[1]
        filename = naming.build_filename(
            profile.filename_template,
            row.extracted,
            orig_stem=stem,
            ext=ext,
            index=index,
            max_filename_length=profile.max_filename_length,
        )
        if filename is None:
            row.status = STATUS_VALIDATION_FAILED
            row.detail = "Sanitised filename is empty"
            rows.append(row)
            continue

        proposed_stem, proposed_ext = os.path.splitext(filename)
        resolved = naming.resolve_collision(
            proposed_stem,
            proposed_ext,
            strategy=profile.collision_strategy,
            file_hash=file_hash,
            sequence_index=index,
            taken=taken_names,
            max_filename_length=profile.max_filename_length,
        )
        if resolved is None:
            row.status = STATUS_COLLISION_UNRESOLVED
            row.detail = f"Could not resolve collision for {filename}"
            rows.append(row)
            continue

        row.proposed_name = resolved
        row.status = STATUS_OK
        taken_names.add(resolved)
        rows.append(row)

    return rows


def cycle_choice_value(current: str, choices: list[str]) -> str:
    if current not in choices:
        return choices[0]
    return choices[(choices.index(current) + 1) % len(choices)]


def recompute_row_filename(row: Row, profile: Profile, all_rows: list[Row], folder: str) -> None:
    """Re-renders one row's proposed_name after its `extracted` values were
    edited in place (e.g. an operator toggled a `choice` field in the Run
    tab). Only OK rows participate; a row that failed extraction has
    nothing to rename regardless of a manual field's value."""
    if row.status != STATUS_OK:
        return

    taken_names = {os.path.basename(p) for p in os.listdir(folder)} if os.path.isdir(folder) else set()
    for other in all_rows:
        if other is not row and other.status == STATUS_OK and other.proposed_name:
            taken_names.add(other.proposed_name)

    stem = os.path.splitext(row.original_name)[0]
    ext = os.path.splitext(row.original_name)[1]
    filename = naming.build_filename(
        profile.filename_template,
        row.extracted,
        orig_stem=stem,
        ext=ext,
        index=row.index,
        max_filename_length=profile.max_filename_length,
    )
    if filename is None:
        row.status = STATUS_VALIDATION_FAILED
        row.proposed_name = ""
        row.detail = "Sanitised filename is empty"
        return

    proposed_stem, proposed_ext = os.path.splitext(filename)
    resolved = naming.resolve_collision(
        proposed_stem,
        proposed_ext,
        strategy=profile.collision_strategy,
        file_hash=row.sha256,
        sequence_index=row.index,
        taken=taken_names,
        max_filename_length=profile.max_filename_length,
    )
    if resolved is None:
        row.status = STATUS_COLLISION_UNRESOLVED
        row.proposed_name = ""
        row.detail = f"Could not resolve collision for {filename}"
        return

    row.proposed_name = resolved


def _apply_output_crop(src: str, dst: str, output_crop, originals_dir: str) -> None:
    """Backs up the untouched original into `_originals/`, then saves a
    cropped copy under the new name. Keeping the original is what makes
    this reversible: once a crop is baked into saved pixels, the discarded
    part of the frame cannot be recovered from the renamed file alone."""
    os.makedirs(originals_dir, exist_ok=True)
    original_name = os.path.basename(src)
    backup_path = os.path.join(originals_dir, original_name)
    if os.path.exists(backup_path):
        raise FileExistsError(f"{backup_path} already exists")

    image = extract.load_image(src)
    px_rect = geometry.profile_rect_to_pixels(output_crop, image.width, image.height)
    cropped_image = image.crop(px_rect)

    save_kwargs = {"quality": 95} if dst.lower().endswith((".jpg", ".jpeg")) else {}
    cropped_image.save(dst, **save_kwargs)
    shutil.copy2(src, backup_path)
    os.remove(src)


def apply(rows: list[Row], folder: str, profile: Profile) -> Report:
    report = Report()
    timestamp = datetime.now(timezone.utc)
    manifest_name = f"_rename_manifest_{timestamp.strftime('%Y%m%d_%H%M%S')}.csv"
    manifest_path = os.path.join(folder, manifest_name)
    report.manifest_path = manifest_path

    field_names_set: set[str] = set()
    for row in rows:
        field_names_set.update(row.extracted.keys())
    extracted_columns = [f"extracted_{n}" for n in sorted(field_names_set)]

    fieldnames = [
        "sha256",
        "original_name",
        "new_name",
        "status",
        *extracted_columns,
        "profile_name",
        "timestamp_utc",
        "output_cropped",
    ]

    originals_dir = os.path.join(folder, ORIGINALS_SUBFOLDER)

    with open(manifest_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        fh.flush()

        for row in rows:
            if row.status != STATUS_OK:
                continue
            src = row.original_path
            dst = os.path.join(folder, row.proposed_name)
            error = ""
            cropped = False
            try:
                if os.path.exists(dst):
                    raise FileExistsError(f"{dst} already exists")
                if profile.output_crop is not None:
                    _apply_output_crop(src, dst, profile.output_crop, originals_dir)
                    cropped = True
                else:
                    os.rename(src, dst)
            except OSError as exc:
                error = str(exc)
                report.failed.append((row, error))
            else:
                report.renamed.append(row)

            manifest_row = {
                "sha256": row.sha256,
                "original_name": row.original_name,
                "new_name": row.proposed_name if not error else "",
                "status": row.status if not error else "RENAME_FAILED",
                "profile_name": profile.profile_name,
                "timestamp_utc": timestamp.isoformat(),
                "output_cropped": "yes" if cropped and not error else "no",
            }
            for n in sorted(field_names_set):
                manifest_row[f"extracted_{n}"] = row.extracted.get(n, "")
            writer.writerow(manifest_row)
            fh.flush()

    return report


def _find_latest_manifest(folder: str) -> str | None:
    candidates = [
        name
        for name in os.listdir(folder)
        if name.startswith("_rename_manifest_") and name.endswith(".csv")
    ]
    if not candidates:
        return None
    candidates.sort()
    return os.path.join(folder, candidates[-1])


@dataclass
class UndoReport:
    restored: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    manifest_path: str = ""


def undo_last_run(folder: str) -> UndoReport:
    report = UndoReport()
    manifest_path = _find_latest_manifest(folder)
    if manifest_path is None:
        report.skipped.append(("", "No manifest found in this folder"))
        return report
    report.manifest_path = manifest_path

    with open(manifest_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    originals_dir = os.path.join(folder, ORIGINALS_SUBFOLDER)

    for row in rows:
        original_name = row.get("original_name", "")
        new_name = row.get("new_name", "")
        if row.get("status") != STATUS_OK or not new_name:
            continue
        current_path = os.path.join(folder, new_name)
        original_path = os.path.join(folder, original_name)
        was_cropped = row.get("output_cropped") == "yes"

        if not os.path.exists(current_path):
            report.skipped.append((new_name, "File no longer present under its renamed name"))
            continue
        if os.path.exists(original_path):
            report.skipped.append((new_name, "Original name is now taken by another file"))
            continue

        try:
            if was_cropped:
                backup_path = os.path.join(originals_dir, original_name)
                if not os.path.exists(backup_path):
                    report.skipped.append((new_name, "Backed-up original not found in _originals"))
                    continue
                os.rename(backup_path, original_path)
                os.remove(current_path)
            else:
                os.rename(current_path, original_path)
            report.restored.append(original_name)
            row["status"] = STATUS_UNDONE
        except OSError as exc:
            report.skipped.append((new_name, str(exc)))

    # Record the undo in the manifest itself. Left as OK, these rows would
    # keep their hashes in _previously_ok_hashes, so the restored files
    # would come back as ALREADY_PROCESSED on the next Preview and could
    # never be renamed again.
    if report.restored:
        with open(manifest_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    return report
