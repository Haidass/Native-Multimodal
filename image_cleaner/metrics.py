"""Native-pixel patch metrics; noise and SNR are proxies, not reference-based PSNR."""
import base64
from functools import lru_cache
import math

import numpy as np
from PIL import Image


@lru_cache(maxsize=1)
def dct_basis():
    x = np.arange(32)
    k = np.arange(8)[:, None]
    return np.cos(np.pi * (2 * x + 1) * k / 64)


def perceptual_hash(image):
    gray = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    dct = dct_basis() @ gray @ dct_basis().T
    values = dct.flatten()
    # Sparse/flat images contain coefficients near numerical zero. Their signs are
    # unstable after JPEG recompression, so ignore values negligible relative to AC.
    tolerance = max(1e-6, float(np.max(np.abs(values[1:]))) * 1e-3)
    values[np.abs(values) <= tolerance] = 0
    bits = values > np.median(values[1:]) + tolerance
    bits[0] = False  # DC is brightness, not spatial content.
    return f"{int.from_bytes(np.packbits(bits).tobytes(), 'big'):016x}"


def difference_hash(image):
    gray = np.asarray(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS))
    return f"{int.from_bytes(np.packbits(gray[:, 1:] > gray[:, :-1]).tobytes(), 'big'):016x}"


def thumbnail_signature(image):
    data = np.asarray(image.resize((16, 16), Image.Resampling.LANCZOS), dtype=np.uint8)
    return base64.b64encode(data.tobytes()).decode("ascii")


def thumbnail_rmse(a, b):
    x = np.frombuffer(base64.b64decode(a), dtype=np.uint8).astype(np.float64)
    y = np.frombuffer(base64.b64decode(b), dtype=np.uint8).astype(np.float64)
    return float(np.sqrt(np.mean((x - y) ** 2)))


def patch_metrics(gray):
    if min(gray.shape) < 3:
        return {"blur_score": None, "noise_sigma": None, "snr_proxy_db": None,
                "blockiness": None, "luminance": float(gray.mean()), "contrast": float(gray.std())}
    center = gray[1:-1, 1:-1]
    up, down = gray[:-2, 1:-1], gray[2:, 1:-1]
    left, right = gray[1:-1, :-2], gray[1:-1, 2:]
    lap = up + down + left + right - 4 * center
    # High-pass kernel has L2 norm 6. Restrict estimation to low-gradient regions.
    high = (gray[:-2, :-2] + gray[:-2, 2:] + gray[2:, :-2] + gray[2:, 2:]
            - 2 * (up + down + left + right) + 4 * center)
    gradient = np.abs(right - left) + np.abs(down - up)
    flat = high[gradient <= np.quantile(gradient, 0.3)]
    sigma = float(np.median(np.abs(flat - np.median(flat))) / (0.6744897501960817 * 6))
    contrast = float(gray.std())
    snr = 20 * math.log10(contrast / sigma) if sigma > 1e-8 and contrast > 1e-8 else None
    # JPEG boundaries are tested at every phase because source crops may shift the 8px grid.
    block_scores = []
    for axis in [0, 1]:
        diff = np.abs(np.diff(gray, axis=axis))
        n = diff.shape[axis]
        if n >= 16:
            phase_scores = []
            indices = np.arange(n)
            for phase in range(8):
                border = indices % 8 == phase
                phase_scores.append(float(np.take(diff, indices[border], axis=axis).mean()
                                          - np.take(diff, indices[~border], axis=axis).mean()))
            block_scores.append(max(phase_scores))
    return {"blur_score": float(lap.var()), "noise_sigma": sigma, "snr_proxy_db": snr,
            "blockiness": float(max(0, np.mean(block_scores))) if block_scores else None,
            "luminance": float(gray.mean()), "contrast": contrast}


def quality_metrics(image, patch_size=256):
    w, h = image.size
    pw, ph = min(w, patch_size), min(h, patch_size)
    positions = sorted({(0, 0), (w-pw, 0), (0, h-ph), (w-pw, h-ph), ((w-pw)//2, (h-ph)//2)})
    patches = [patch_metrics(np.asarray(image.crop((x, y, x+pw, y+ph)).convert("L"),
                                        dtype=np.float64)) for x, y in positions]
    result = {}
    for key in patches[0]:
        values = [p[key] for p in patches if p[key] is not None]
        result[key] = float(np.median(values)) if values else None
    result["metric_patch_count"] = len(patches)
    return result
