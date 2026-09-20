"""Profile dataclasses, load, save, version check. No GUI imports."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VALID_TYPES = {"digits", "letters", "alphanumeric", "date", "text", "choice"}


class ProfileError(ValueError):
    """Raised for invalid or unloadable profiles."""


@dataclass
class ReferenceImage:
    width: int
    height: int
    aspect_ratio: float
    example_filename: str

    @classmethod
    def from_dict(cls, d: dict) -> "ReferenceImage":
        return cls(
            width=int(d["width"]),
            height=int(d["height"]),
            aspect_ratio=float(d["aspect_ratio"]),
            example_filename=str(d["example_filename"]),
        )


@dataclass
class LayoutGuard:
    enabled: bool = True
    aspect_tolerance: float = 0.02

    @classmethod
    def from_dict(cls, d: dict | None) -> "LayoutGuard":
        if not d:
            return cls()
        return cls(
            enabled=bool(d.get("enabled", True)),
            aspect_tolerance=float(d.get("aspect_tolerance", 0.02)),
        )


@dataclass
class Field:
    name: str
    type: str
    rect: tuple[float, float, float, float]
    required: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not FIELD_NAME_RE.match(self.name):
            raise ProfileError(
                f"Field name {self.name!r} must match ^[a-z][a-z0-9_]*$"
            )
        if self.type not in VALID_TYPES:
            raise ProfileError(f"Field {self.name!r} has unknown type {self.type!r}")
        x0, y0, x1, y1 = self.rect
        if not (x0 < x1 and y0 < y1):
            raise ProfileError(f"Field {self.name!r} has a non-normalised rect")

    @classmethod
    def from_dict(cls, d: dict) -> "Field":
        rect = d["rect"]
        return cls(
            name=d["name"],
            type=d["type"],
            rect=(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])),
            required=bool(d.get("required", True)),
            options=dict(d.get("options", {})),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "rect": list(self.rect),
            "required": self.required,
            "options": self.options,
        }


@dataclass
class Profile:
    profile_name: str
    reference_image: ReferenceImage
    fields: list[Field]
    filename_template: str
    schema_version: int = SCHEMA_VERSION
    created_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    collision_strategy: str = "hash_suffix"
    max_filename_length: int = 120
    layout_guard: LayoutGuard = field(default_factory=LayoutGuard)
    output_crop: tuple[float, float, float, float] | None = None
    """Optional fixed crop applied to the saved (renamed) image on Apply.
    Fractions of the reference image, same convention as a field's rect.
    None means the output keeps the full original frame."""

    def __post_init__(self) -> None:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            dupes = {n for n in names if names.count(n) > 1}
            raise ProfileError(f"Duplicate field name(s): {sorted(dupes)}")

    def field_names(self) -> set[str]:
        return {f.name for f in self.fields}

    def get_field(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "profile_name": self.profile_name,
            "created_utc": self.created_utc,
            "reference_image": asdict(self.reference_image),
            "fields": [f.to_dict() for f in self.fields],
            "filename_template": self.filename_template,
            "collision_strategy": self.collision_strategy,
            "max_filename_length": self.max_filename_length,
            "layout_guard": asdict(self.layout_guard),
            "output_crop": list(self.output_crop) if self.output_crop else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        version = d.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ProfileError(
                f"Unsupported profile schema_version {version!r}; "
                f"this app supports version {SCHEMA_VERSION}"
            )
        return cls(
            schema_version=version,
            profile_name=d["profile_name"],
            created_utc=d.get("created_utc", ""),
            reference_image=ReferenceImage.from_dict(d["reference_image"]),
            fields=[Field.from_dict(fd) for fd in d.get("fields", [])],
            filename_template=d["filename_template"],
            collision_strategy=d.get("collision_strategy", "hash_suffix"),
            max_filename_length=int(d.get("max_filename_length", 120)),
            layout_guard=LayoutGuard.from_dict(d.get("layout_guard")),
            output_crop=tuple(d["output_crop"]) if d.get("output_crop") else None,
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProfileError(f"Profile file is not valid JSON: {exc}") from exc
        return cls.from_dict(data)


def default_profiles_dir() -> Path:
    return Path.home() / "Documents" / "ImageRenamer" / "profiles"
