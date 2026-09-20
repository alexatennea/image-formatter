"""Filename template resolution, sanitisation and collision handling.
No GUI imports."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")
ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE_RE = re.compile(r"\s+")
UNDERSCORE_DASH_RUN_RE = re.compile(r"[_\-]{2,}")
STRIP_EDGE_RE = re.compile(r"^[.\s_-]+|[.\s_-]+$")
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

SPECIAL_TOKENS = {"orig", "n", "today"}


class TemplateError(ValueError):
    pass


def template_tokens(template: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(template))


def validate_template(template: str, field_names: set[str]) -> None:
    known = field_names | SPECIAL_TOKENS
    for token in template_tokens(template):
        if token not in known:
            raise TemplateError(f"Unknown field {{{token}}} in template")


def render_template(
    template: str,
    values: dict[str, str],
    *,
    orig: str,
    index: int,
    today: date | None = None,
) -> str:
    context = dict(values)
    context["orig"] = orig
    context["n"] = f"{index:03d}"
    context["today"] = (today or date.today()).strftime("%Y-%m-%d")

    def replace(match: re.Match) -> str:
        token = match.group(1)
        if token not in context:
            raise TemplateError(f"Unknown field {{{token}}} in template")
        return str(context[token])

    return PLACEHOLDER_RE.sub(replace, template)


def sanitise_stem(stem: str, max_length: int) -> str:
    """Applies the sanitisation pipeline to an assembled filename stem
    (no extension). Returns "" if nothing survives -- callers must treat
    that as VALIDATION_FAILED, never fall back to the original name."""
    s = ILLEGAL_CHARS_RE.sub("-", stem)
    s = WHITESPACE_RE.sub("_", s)
    s = UNDERSCORE_DASH_RUN_RE.sub(lambda m: m.group(0)[0], s)
    s = STRIP_EDGE_RE.sub("", s)
    if s.upper() in RESERVED_NAMES:
        s = "_" + s
    if max_length > 0 and len(s) > max_length:
        truncated = s[:max_length]
        # cut at a separator if one falls within the last 10 characters
        tail = truncated[-10:]
        sep_pos = max(tail.rfind("_"), tail.rfind("-"))
        if sep_pos != -1:
            cut_at = len(truncated) - (len(tail) - sep_pos)
            truncated = truncated[:cut_at]
        s = STRIP_EDGE_RE.sub("", truncated)
    return s


def build_filename(
    template: str,
    values: dict[str, str],
    *,
    orig_stem: str,
    ext: str,
    index: int,
    max_filename_length: int,
    today: date | None = None,
) -> str | None:
    """Renders, sanitises and reassembles a full filename with extension.
    Returns None if the sanitised stem is empty (VALIDATION_FAILED)."""
    raw = render_template(template, values, orig=orig_stem, index=index, today=today)
    max_stem_length = max(0, max_filename_length - len(ext))
    stem = sanitise_stem(raw, max_stem_length)
    if not stem:
        return None
    return stem + ext


def sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class CollisionContext:
    existing_names: set[str]
    """Names already taken: files on disk in the target folder plus names
    already assigned to earlier rows in this batch."""


def resolve_collision(
    proposed_stem: str,
    ext: str,
    *,
    strategy: str,
    file_hash: str,
    sequence_index: int,
    taken: set[str],
    max_filename_length: int,
) -> str | None:
    """Returns a final filename not in `taken`, or None if unresolved
    (COLLISION_UNRESOLVED)."""
    candidate = proposed_stem + ext
    if candidate not in taken:
        return candidate

    if strategy == "hash_suffix":
        suffix = "_" + file_hash[:6]
    elif strategy == "sequence":
        suffix = f"_{sequence_index:02d}"
    else:
        return None

    max_stem_length = max(0, max_filename_length - len(ext) - len(suffix))
    trimmed_stem = sanitise_stem(proposed_stem[:max_stem_length] if max_stem_length else "", max_stem_length) or proposed_stem[:max_stem_length]
    candidate = trimmed_stem + suffix + ext
    if candidate in taken:
        return None
    return candidate
