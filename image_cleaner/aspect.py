"""Deterministic width:height matching with exact inclusive tolerance boundaries."""
from fractions import Fraction
from functools import lru_cache


@lru_cache(maxsize=128)
def tolerance_fraction(tolerance):
    return Fraction(str(tolerance))


def assess_aspect(row, config):
    result = {"aspect_bucket": None, "aspect_bucket_nearest": None,
              "aspect_bucket_deviation": None, "aspect_bucket_match": None}
    if not config.aspect_buckets:
        return result
    distances = []
    for bucket in config.aspect_buckets:
        numerator, denominator = map(int, bucket.split(":"))
        a, b = row["width"] * denominator, row["height"] * numerator
        distances.append((Fraction(abs(a-b), min(a, b)), bucket))
    # min is stable: ties follow the fixed preset order normalized by Config.
    deviation, nearest = min(distances, key=lambda item: item[0])
    matched = deviation == 0 if config.aspect_mode == "strict" else deviation <= tolerance_fraction(config.aspect_tolerance)
    result.update(aspect_bucket=nearest if matched else None, aspect_bucket_nearest=nearest,
                  aspect_bucket_deviation=float(deviation), aspect_bucket_match=matched)
    return result
