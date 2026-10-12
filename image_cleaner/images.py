from io import BytesIO
import hashlib
import re
import unicodedata
import warnings

from PIL import Image, ImageCms, ImageOps

from .config import Config
from .metrics import difference_hash, perceptual_hash, quality_metrics, thumbnail_signature


def normalize_text(text, form):
    if text is None:
        return None
    if not isinstance(text, str):
        raise ValueError("text must be a string or null")
    if form != "none":
        text = unicodedata.normalize(form, text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def canonical_image(path, config):
    """Load one static image, transpose EXIF, color-manage, flatten alpha, return RGB."""
    Image.MAX_IMAGE_PIXELS = config.max_decode_pixels
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as source:
            w, h = source.size
            if w * h > config.max_decode_pixels:
                raise ValueError("decode_pixel_limit")
            metadata = {"original_width": w, "original_height": h, "original_mode": source.mode,
                        "original_format": source.format, "frame_count": getattr(source, "n_frames", 1)}
            if metadata["frame_count"] > 1 and config.reject_animated:
                raise ValueError("animated_or_multipage")
            source.load()  # Detect truncated data even if metadata parsing succeeded.
            oriented = ImageOps.exif_transpose(source)
            metadata["exif_transposed"] = source.getexif().get(274, 1) not in (1, None)
            alpha = None
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                alpha = oriented.convert("RGBA").getchannel("A")
            metadata["alpha_flattened"] = alpha is not None
            icc = oriented.info.get("icc_profile")
            metadata["icc_converted"] = False
            if icc and config.convert_icc:
                # RGB profiles cannot consume RGBA/P directly; CMYK profiles require CMYK input.
                mode = oriented.mode if oriented.mode in {"RGB", "CMYK", "LAB", "L"} else "RGB"
                oriented = ImageCms.profileToProfile(oriented.convert(mode),
                    ImageCms.ImageCmsProfile(BytesIO(icc)), ImageCms.createProfile("sRGB"), outputMode="RGB")
                metadata["icc_converted"] = True
            rgb = oriented.convert("RGB")
            if alpha is not None:
                background = Image.new("RGB", rgb.size, config.alpha_background)
                background.paste(rgb, mask=alpha)
                rgb = background
            return rgb.copy(), metadata


def resolution_matches(row, resolution):
    if resolution is None:
        return True
    if resolution.endswith("p"):
        return row["short_edge"] >= int(resolution[:-1])
    width, height = map(int, resolution.split("x"))
    return row["width"] == width and row["height"] == height


def filter_reasons(row, config):
    from .aspect import assess_aspect
    row.update(assess_aspect(row, config))
    checks = [("short_edge", config.min_short_edge, "min_short_edge", "min"),
              ("long_edge", config.max_long_edge, "max_long_edge", "max"),
              ("pixels", config.min_pixels, "min_pixels", "min"),
              ("pixels", config.max_pixels, "max_pixels", "max"),
              ("aspect_ratio", config.min_aspect_ratio, "min_aspect_ratio", "min"),
              ("aspect_ratio", config.max_aspect_ratio, "max_aspect_ratio", "max"),
              ("blur_score", config.min_blur_score, "min_blur_score", "min"),
              ("noise_sigma", config.max_noise_sigma, "max_noise_sigma", "max"),
              ("snr_proxy_db", config.min_snr_proxy_db, "min_snr_proxy_db", "min"),
              ("blockiness", config.max_blockiness, "max_blockiness", "max")]
    reasons = []
    if not resolution_matches(row, config.resolution):
        reasons.append("resolution_mismatch")
    if row["aspect_bucket_match"] is False:
        reasons.append("aspect_bucket_mismatch")
    for key, threshold, name, direction in checks:
        value = row.get(key)
        if threshold is not None and value is not None:
            if (direction == "min" and value < threshold) or (direction == "max" and value > threshold):
                reasons.append(name)
    text = row.get("text")
    if config.min_text_chars is not None and len(text or "") < config.min_text_chars:
        reasons.append("min_text_chars")
    if config.max_text_chars is not None and len(text or "") > config.max_text_chars:
        reasons.append("max_text_chars")
    if any(re.search(p, text or "") for p in config.forbidden_text_patterns):
        reasons.append("forbidden_text_pattern")
    return reasons


def analyze_one(task):
    item, path, config_dict = task
    config = Config(**config_dict)
    row = dict(item)
    row.update({"source_path": str(path), "status": "error", "reasons": [], "warnings": []})
    try:
        row["text"] = normalize_text(item.get("text"), config.text_unicode_form)
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        row["file_sha256"] = digest.hexdigest()
        rgb, metadata = canonical_image(path, config)
        row.update(metadata)
        w, h = rgb.size
        row.update(width=w, height=h, short_edge=min(w, h), long_edge=max(w, h),
                   pixels=w*h, aspect_ratio=w/h)
        pixel_hash = hashlib.sha256(f"RGB:{w}:{h}:".encode())
        # Hash in strips to avoid making another full-size image bytes allocation.
        for y in range(0, h, 128):
            pixel_hash.update(rgb.crop((0, y, w, min(y+128, h))).tobytes())
        row["pixel_sha256"] = pixel_hash.hexdigest()
        row["phash"] = perceptual_hash(rgb)
        row["dhash"] = difference_hash(rgb)
        row["thumbnail"] = thumbnail_signature(rgb)
        row.update(quality_metrics(rgb, config.metric_patch_size))
        if row["snr_proxy_db"] is None:
            row["warnings"].append("snr_proxy_undefined")
        row["reasons"] = filter_reasons(row, config)
        row["status"] = "rejected" if row["reasons"] else "candidate"
        rgb.close()
    except Exception as exc:
        row["status"] = "error"
        row["reasons"] = ["decode_or_processing_error"]
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row
