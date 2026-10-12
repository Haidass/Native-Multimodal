from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
from pathlib import Path
import re

ASPECT_BUCKETS = ("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9")


def normalize_aspect_buckets(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().lower()
        if value == "none":
            return None
        if value == "all":
            return ASPECT_BUCKETS
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, (tuple, list)) or not value:
        raise ValueError("aspect_buckets must be a nonempty list, comma-separated buckets, all, or none")
    if any(not isinstance(bucket, str) or bucket not in ASPECT_BUCKETS for bucket in value):
        raise ValueError(f"Unsupported aspect bucket; choose from {ASPECT_BUCKETS}")
    return tuple(bucket for bucket in ASPECT_BUCKETS if bucket in value)


def normalize_resolution(value):
    """Np is an inclusive short-edge lower bound; WxH is exact oriented width and height."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("resolution must be a string such as 256p or 256x256, or null")
    value = value.strip().lower().replace("\u00d7", "x")
    if value in {"any", "none"}:
        return None
    if re.fullmatch(r"[1-9][0-9]*p", value) or re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", value):
        return value
    raise ValueError("Invalid resolution: use 256p (short edge >=256), 256x256 (exact width/height), or any")


@dataclass(frozen=True)
class Config:
    # None means measure only. Thresholds must be calibrated on the target corpus.
    resolution: str | None = None
    aspect_buckets: tuple[str, ...] | None = None
    aspect_mode: str = "strict"
    aspect_tolerance: float = 0.05
    min_short_edge: int | None = None
    max_long_edge: int | None = None
    min_pixels: int | None = None
    max_pixels: int | None = None
    min_aspect_ratio: float | None = None
    max_aspect_ratio: float | None = None
    min_blur_score: float | None = None
    max_noise_sigma: float | None = None
    min_snr_proxy_db: float | None = None
    max_blockiness: float | None = None
    min_text_chars: int | None = None
    max_text_chars: int | None = None
    forbidden_text_patterns: tuple[str, ...] = ()
    reject_animated: bool = True
    max_decode_pixels: int = 100_000_000
    metric_patch_size: int = 256
    alpha_background: tuple[int, int, int] = (255, 255, 255)
    convert_icc: bool = True
    text_unicode_form: str = "NFC"
    phash_distance: int = 4
    dhash_distance: int = 4
    near_rgb_rmse: float = 8.0
    near_aspect_log_tolerance: float = 0.03
    deduplicate: bool = True
    near_deduplicate: bool = True
    duplicate_scope: str = "image_text"
    export_max_edge: int | None = None
    export_format: str = "png"
    export_jpeg_quality: int = 95

    def __post_init__(self):
        object.__setattr__(self, "resolution", normalize_resolution(self.resolution))
        object.__setattr__(self, "aspect_buckets", normalize_aspect_buckets(self.aspect_buckets))
        for name in ("alpha_background", "forbidden_text_patterns"):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{name} must be an array")
            object.__setattr__(self, name, tuple(value))
        if self.aspect_mode not in {"strict", "loose"}:
            raise ValueError("aspect_mode must be strict or loose")
        numeric = [f.name for f in fields(self) if f.name.startswith(("min_", "max_"))]
        numeric += ["near_rgb_rmse", "near_aspect_log_tolerance", "aspect_tolerance"]
        for name in numeric:
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value)):
                raise ValueError(f"{name} must be a finite number or null")
            if value is not None and name != "min_snr_proxy_db" and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.aspect_tolerance is None:
            raise ValueError("aspect_tolerance must be a finite non-negative number")
        for lo, hi in [("min_aspect_ratio", "max_aspect_ratio"), ("min_pixels", "max_pixels"),
                       ("min_text_chars", "max_text_chars")]:
            if getattr(self, lo) is not None and getattr(self, hi) is not None:
                if getattr(self, lo) > getattr(self, hi):
                    raise ValueError(f"{lo} must not exceed {hi}")
        for name in ["min_short_edge", "max_long_edge", "min_pixels", "max_pixels",
                     "min_text_chars", "max_text_chars", "export_max_edge", "max_decode_pixels",
                     "metric_patch_size", "phash_distance", "dhash_distance", "export_jpeg_quality"]:
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        if not 0 <= self.phash_distance <= 8 or not 0 <= self.dhash_distance <= 64:
            raise ValueError("phash_distance must be 0..8; dhash_distance must be 0..64")
        if self.metric_patch_size < 16 or self.max_decode_pixels < 1:
            raise ValueError("metric_patch_size >= 16 and max_decode_pixels >= 1 required")
        if self.export_max_edge is not None and self.export_max_edge < 1:
            raise ValueError("export_max_edge must be positive")
        if self.export_format not in {"png", "jpeg"} or not 1 <= self.export_jpeg_quality <= 100:
            raise ValueError("export_format must be png/jpeg and JPEG quality 1..100")
        if self.text_unicode_form not in {"NFC", "NFKC", "none"}:
            raise ValueError("text_unicode_form must be NFC, NFKC or none")
        if self.duplicate_scope not in {"image", "image_text"}:
            raise ValueError("duplicate_scope must be image or image_text")
        if len(self.alpha_background) != 3 or any(type(v) is not int or not 0 <= v <= 255
                                                 for v in self.alpha_background):
            raise ValueError("alpha_background must contain three integers in 0..255")
        for name in ["reject_animated", "convert_icc", "deduplicate", "near_deduplicate"]:
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        for pattern in self.forbidden_text_patterns:
            if not isinstance(pattern, str):
                raise ValueError("forbidden_text_patterns must contain strings")
            re.compile(pattern)

    def to_dict(self):
        return asdict(self)

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


def load_config(path=None):
    values = {} if path is None else json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(values, dict):
        raise ValueError("Configuration must be a JSON object")
    unknown = set(values) - {f.name for f in fields(Config)}
    if unknown:
        raise ValueError(f"Unknown configuration fields: {sorted(unknown)}")
    for key in ["alpha_background", "forbidden_text_patterns"]:
        if key in values:
            if not isinstance(values[key], list):
                raise ValueError(f"{key} must be a JSON array")
            values[key] = tuple(values[key])
    return Config(**values)
