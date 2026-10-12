from collections import Counter
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from PIL import Image

from . import __version__
from .config import Config
from .images import analyze_one, canonical_image, filter_reasons, resolution_matches
from .io import (atomic_text, connect, exclusive_lock, get_meta, json_line, manifest_info,
                 manifest_items, parallel_bounded, resolve_source, set_meta, sha256_file, write_json)
from .metrics import thumbnail_rmse
from .stats import DatasetStats, write_html


def analyze(manifest, output, config, workers=1, rank=0, world_size=1, root=None, resume=False):
    if workers < 1 or world_size < 1 or not 0 <= rank < world_size:
        raise ValueError("workers/world-size must be positive and 0 <= rank < world-size")
    info, base = manifest_info(manifest, root)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output/f"shard-{rank:05d}-of-{world_size:05d}.sqlite"
    expected_count = max(0, (info["count"] - 1 - rank) // world_size + 1)
    identity = {"schema_version": 1, "software_version": __version__, "manifest_sha256": info["sha256"],
                "input_count": info["count"], "rank": rank, "world_size": world_size,
                "config_sha256": config.fingerprint(), "config": config.to_dict()}
    # JSON normalizes tuple/list values for consistent equality after restart.
    identity = json.loads(json.dumps(identity))
    with exclusive_lock(str(path) + ".lock"):
        exists = path.exists()
        if exists and not resume:
            raise ValueError(f"Shard already exists: {path}; use --resume")
        db = connect(path)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS records (seq INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            stored = get_meta(db, "identity")
            if exists and stored != identity:
                raise ValueError("Resume identity mismatch (manifest, config, ranks or software changed)")
            set_meta(db, "identity", identity)
            set_meta(db, "complete", False)
            db.commit()
            config_dict = config.to_dict()
            def tasks():
                for item in manifest_items(manifest):
                    if item["seq"] % world_size != rank:
                        continue
                    if db.execute("SELECT 1 FROM records WHERE seq=?", (item["seq"],)).fetchone():
                        continue
                    yield item, str(resolve_source(base, item)), config_dict
            count = 0
            started = time.monotonic()
            for row in parallel_bounded(analyze_one, tasks(), workers):
                db.execute("INSERT INTO records VALUES (?,?)", (row["seq"], json_line(row)))
                count += 1
                if count % 100 == 0:
                    db.commit()
                    print(f"rank {rank}: analyzed {count} new images ({count/max(.01, time.monotonic()-started):.1f}/s)", flush=True)
            db.commit()
            total = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            if total != expected_count:
                raise ValueError(f"Shard incomplete: expected {expected_count}, got {total}")
            set_meta(db, "complete", True)
            db.commit()
            with atomic_text(path.with_suffix(".jsonl")) as handle:
                for (payload,) in db.execute("SELECT payload FROM records ORDER BY seq"):
                    handle.write(payload)
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            db.close()
    return {"shard": str(path), "records": total, "new_records": count}


def hash_bands(value, radius):
    """Pigeonhole guarantee: hashes at distance <=r share one of r+1 exact bands."""
    integer = int(value, 16)
    parts = radius+1
    offset = 0
    for part in range(parts):
        width = 64//parts + (1 if part < 64 % parts else 0)
        yield part, f"{(integer >> offset) & ((1 << width)-1):x}"
        offset += width


class DuplicateIndex:
    """Disk-backed global index; only eligible retained representatives enter it."""
    def __init__(self, db, config):
        self.db, self.config = db, config
        db.execute("CREATE TABLE hashes (kind TEXT, hash TEXT, owner INTEGER, PRIMARY KEY(kind,hash))")
        db.execute("CREATE TABLE bands (part INTEGER, value TEXT, owner INTEGER)")
        db.execute("CREATE INDEX band_lookup ON bands(part,value)")
        db.execute("CREATE TABLE representatives (seq INTEGER PRIMARY KEY, payload TEXT NOT NULL)")

    def find(self, row):
        if not self.config.deduplicate:
            return None
        for kind, key in [("file", "file_sha256"), ("pixels", "pixel_sha256")]:
            result = self.db.execute("SELECT owner FROM hashes WHERE kind=? AND hash=?", (kind, self.key(row, key))).fetchone()
            if result:
                return result[0], "exact_file" if kind == "file" else "exact_pixels", None
        if not self.config.near_deduplicate:
            return None
        bands = list(hash_bands(row["phash"], self.config.phash_distance))
        predicates = " OR ".join("(part=? AND value=?)" for _ in bands)
        args = [v for band in bands for v in band]
        query = ("SELECT r.seq,r.payload FROM representatives r JOIN "
                 f"(SELECT DISTINCT owner FROM bands WHERE {predicates}) b ON r.seq=b.owner ORDER BY r.seq")
        # Stream candidates from SQLite; repetitive corpora can produce large candidate sets.
        for seq, payload in self.db.execute(query, args):
            other = json.loads(payload)
            if self.config.duplicate_scope == "image_text" and (row.get("text") or "") != (other.get("text") or ""):
                continue
            distance = (int(row["phash"], 16) ^ int(other["phash"], 16)).bit_count()
            if distance > self.config.phash_distance:
                continue
            if (int(row["dhash"], 16) ^ int(other["dhash"], 16)).bit_count() > self.config.dhash_distance:
                continue
            if abs(math.log(row["aspect_ratio"]/other["aspect_ratio"])) > self.config.near_aspect_log_tolerance:
                continue
            rmse = thumbnail_rmse(row["thumbnail"], other["thumbnail"])
            if rmse <= self.config.near_rgb_rmse:
                return seq, "near_perceptual", {"phash_distance": distance, "thumbnail_rgb_rmse": rmse}
        return None

    def insert(self, row):
        if not self.config.deduplicate:
            return
        for kind, key in [("file", "file_sha256"), ("pixels", "pixel_sha256")]:
            self.db.execute("INSERT INTO hashes VALUES (?,?,?)", (kind, self.key(row, key), row["seq"]))
        if self.config.near_deduplicate:
            self.db.execute("INSERT INTO representatives VALUES (?,?)", (row["seq"], json_line(row)))
            self.db.executemany("INSERT INTO bands VALUES (?,?,?)", [(p, value, row["seq"])
                                for p, value in hash_bands(row["phash"], self.config.phash_distance)])

    def key(self, row, key):
        if self.config.duplicate_scope == "image":
            return row[key]
        text_hash = hashlib.sha256((row.get("text") or "").encode("utf-8")).hexdigest()
        return row[key] + ":" + text_hash


ANALYSIS_FIELDS = ["reject_animated", "max_decode_pixels", "metric_patch_size", "alpha_background",
                   "convert_icc", "text_unicode_form"]


def merge(shard_dir, output, config=None, resolution=None, aspect_buckets=None, aspect_mode=None, aspect_tolerance=None):
    shard_dir, output = Path(shard_dir).resolve(), Path(output).resolve()
    paths = sorted(shard_dir.glob("shard-*-of-*.sqlite"))
    if not paths:
        raise ValueError("No shard SQLite databases found")
    output.mkdir(parents=True, exist_ok=True)
    destination = output/"curation.sqlite"
    with exclusive_lock(output/"merge.lock"):
        if destination.exists():
            raise ValueError("Merge output already exists; choose a new output directory to re-filter")
        identities = []
        for path in paths:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                identity = get_meta(source, "identity")
                if not get_meta(source, "complete"):
                    raise ValueError(f"Shard is incomplete: {path}")
                identities.append(identity)
        first = identities[0]
        if any({k: v for k, v in identity.items() if k != "rank"} !=
               {k: v for k, v in first.items() if k != "rank"} for identity in identities):
            raise ValueError("Shard identities do not match")
        if sorted(i["rank"] for i in identities) != list(range(first["world_size"])):
            raise ValueError("Missing or duplicate shard ranks; all ranks are required for global deduplication")
        source_config = Config(**first["config"])
        config = config or source_config
        overrides = {name: value for name, value in
                     (("resolution", resolution), ("aspect_buckets", aspect_buckets),
                      ("aspect_mode", aspect_mode), ("aspect_tolerance", aspect_tolerance)) if value is not None}
        config = replace(config, **overrides)
        for key in ANALYSIS_FIELDS:
            if json.dumps(getattr(config, key)) != json.dumps(getattr(source_config, key)):
                raise ValueError(f"Cannot re-filter with changed analysis setting {key}; analyze again")
        temporary = output/"curation.building.sqlite"
        if temporary.exists():
            raise ValueError("Previous incomplete merge exists; choose a new output directory")
        db = connect(temporary)
        try:
            db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE records (seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,"
                       "pixels INTEGER NOT NULL, noise REAL NOT NULL, blur REAL NOT NULL)")
            for path, identity in zip(paths, identities):
                shard_count = 0
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                    for seq, payload in source.execute("SELECT seq,payload FROM records ORDER BY seq"):
                        row = json.loads(payload)
                        if seq != row["seq"] or seq % first["world_size"] != identity["rank"] or not 0 <= seq < first["input_count"]:
                            raise ValueError(f"Invalid shard sequence: {path}, {seq}")
                        if row["status"] != "error":
                            row["reasons"] = filter_reasons(row, config)
                            row["status"] = "rejected" if row["reasons"] else "candidate"
                        db.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", (seq, json_line(row), row["status"],
                                   row.get("pixels", 0), row.get("noise_sigma") or 0, row.get("blur_score") or 0))
                        shard_count += 1
                        if shard_count % 1000 == 0:
                            db.commit()
                expected = max(0, (first["input_count"]-1-identity["rank"])//first["world_size"]+1)
                if shard_count != expected:
                    raise ValueError(f"Wrong record count in {path}: {shard_count}, expected {expected}")
            total = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            if total != first["input_count"]:
                raise ValueError("Global record count does not match manifest")
            db.execute("CREATE INDEX candidate_order ON records(status,pixels DESC,noise,blur DESC,seq)")
            db.commit()
            index = DuplicateIndex(db, config)
            processed = 0
            # Fetch bounded batches, then update status. The indexed candidate set shrinks.
            while True:
                batch = db.execute("SELECT seq,payload,pixels,noise,blur FROM records WHERE status='candidate' "
                                   "ORDER BY pixels DESC,noise,blur DESC,seq LIMIT 500").fetchall()
                if not batch:
                    break
                for seq, payload, pixels, noise, blur in batch:
                    row = json.loads(payload)
                    duplicate = index.find(row)
                    if duplicate:
                        owner, kind, metrics = duplicate
                        row.update(status="duplicate", duplicate_of=f"sample-{owner:012d}",
                                   duplicate_kind=kind, duplicate_metrics=metrics, reasons=[kind])
                    else:
                        row["status"] = "kept"
                        index.insert(row)
                    db.execute("UPDATE records SET payload=?,status=? WHERE seq=?", (json_line(row), row["status"], seq))
                    processed += 1
                db.commit()
                print(f"global dedup: {processed} eligible images processed", flush=True)
            before, after = DatasetStats(), DatasetStats()
            resolution_matched = 0
            aspect_matched = 0
            bucket_before = dict.fromkeys(config.aspect_buckets or (), 0)
            bucket_after = dict.fromkeys(config.aspect_buckets or (), 0)
            destinations = {status: output/f"{status}.jsonl" for status in ["kept", "rejected", "duplicate", "error"]}
            # SQLite is the authoritative output. JSONL and reports are streamed from it.
            from contextlib import ExitStack
            with ExitStack() as stack:
                handles = {status: stack.enter_context(atomic_text(path)) for status, path in destinations.items()}
                for (payload,) in db.execute("SELECT payload FROM records ORDER BY seq"):
                    row = json.loads(payload)
                    before.add(row)
                    if row["status"] != "error":
                        if row["aspect_bucket_match"] is not False:
                            aspect_matched += 1
                        if row["aspect_bucket"] is not None:
                            bucket_before[row["aspect_bucket"]] += 1
                            if row["status"] == "kept":
                                bucket_after[row["aspect_bucket"]] += 1
                    if row["status"] != "error" and resolution_matches(row, config.resolution):
                        resolution_matched += 1
                    if row["status"] == "kept":
                        after.add(row)
                    handles[row["status"]].write(payload)
            counts = before.statuses
            decoded = before.decoded
            eligible = counts["kept"] + counts["duplicate"]
            def rate(numerator, denominator):
                return numerator / denominator if denominator else None
            stage_metrics = {
                "processing_success_count": decoded,
                "processing_success_rate": rate(decoded, total),
                "resolution_matched_count": resolution_matched,
                "resolution_mismatched_count": decoded-resolution_matched,
                "resolution_pass_rate": rate(resolution_matched, decoded),
                "aspect_bucket_matched_count": aspect_matched,
                "aspect_bucket_mismatched_count": decoded-aspect_matched,
                "aspect_bucket_pass_rate": rate(aspect_matched, decoded),
                "rules_eligible_count": eligible,
                "rules_pass_rate": rate(eligible, decoded),
                "duplicate_rate_among_eligible": rate(counts["duplicate"], eligible),
                "final_keep_rate": rate(counts["kept"], total),
            }
            report = {"schema_version": 1, "software_version": __version__, "manifest_sha256": first["manifest_sha256"],
                      "config": config.to_dict(), "input_count": total, "kept_count": counts["kept"],
                      "rejected_count": counts["rejected"], "duplicate_count": counts["duplicate"], "error_count": counts["error"],
                      "dedup_representative_order": "pixels DESC, noise_sigma ASC, blur_score DESC, seq ASC",
                      "resolution_filter": {"requested": config.resolution, "basis": "dimensions_after_exif_transpose",
                                            "mode": "short_edge_gte" if config.resolution and config.resolution.endswith("p")
                                                    else "width_height" if config.resolution else "disabled",
                                            "comparison": ">=" if config.resolution and config.resolution.endswith("p")
                                                          else "==" if config.resolution else None},
                      "stage_metrics": stage_metrics,
                      "aspect_bucket_filter": {"enabled": config.aspect_buckets is not None,
                                               "selected_buckets": config.aspect_buckets or (),
                                               "mode": config.aspect_mode, "tolerance": config.aspect_tolerance,
                                               "basis": "dimensions_after_exif_transpose",
                                               "deviation": "max(r/b,b/r)-1",
                                               "counts_before": bucket_before, "counts_after": bucket_after},
                      "before": before.result(), "after": after.result()}
            set_meta(db, "report", report)
            set_meta(db, "complete", True)
            db.commit()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            # Leave no WAL dependency when publishing the database.
            db.execute("PRAGMA journal_mode=DELETE")
        finally:
            db.close()
        temporary.replace(destination)
        write_json(output/"report.json", report)
        write_html(output/"report.html", report)
    return {k: report[k] for k in ["input_count", "kept_count", "rejected_count", "duplicate_count", "error_count"]}


def export_images(curated_dir, output, workers=1, root=None, resume=False):
    curated_dir, output = Path(curated_dir).resolve(), Path(output).resolve()
    if workers < 1:
        raise ValueError("workers must be positive")
    output.mkdir(parents=True, exist_ok=True)
    report = json.loads((curated_dir/"report.json").read_text(encoding="utf-8"))
    config = Config(**report["config"])
    fingerprint = hashlib.sha256(json.dumps({"manifest": report["manifest_sha256"], "config": config.to_dict()},
                                           sort_keys=True).encode()).hexdigest()
    with exclusive_lock(output/"export.lock"):
        meta_path = output/"export.meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not resume or meta["fingerprint"] != fingerprint:
                raise ValueError("Export already exists or configuration differs; choose a new output directory")
        elif any(output.iterdir()):
            # Only the lock is present for a new destination.
            if any(p.name != "export.lock" for p in output.iterdir()):
                raise ValueError("Export destination must be empty")
        write_json(meta_path, {"fingerprint": fingerprint, "complete": False})
        def tasks():
            for item in manifest_items(curated_dir/"kept.jsonl"):
                path = resolve_source(Path(root).resolve(), item) if root else Path(item["source_path"])
                target = output/"images"/f'{item["seq"]//10000:06d}'/(item["id"] + "." + ("jpg" if config.export_format == "jpeg" else "png"))
                yield item, str(path), str(target), config.to_dict(), resume
        counts = Counter()
        with atomic_text(output/"exported.jsonl") as retained, atomic_text(output/"export_errors.jsonl") as errors:
            for result in parallel_bounded(export_one, tasks(), workers):
                status = result.pop("export_status")
                counts[status] += 1
                if status == "ok":
                    result["source_path"] = result.pop("path")
                    result["path"] = Path(result.pop("output_path")).relative_to(output).as_posix()
                    retained.write(json_line(result))
                else:
                    errors.write(json_line(result))
        write_json(meta_path, {"fingerprint": fingerprint, "complete": counts["error"] == 0,
                              "exported": counts["ok"], "errors": counts["error"]})
    return dict(counts)


def export_one(task):
    item, source, target, config_dict, resume = task
    result = {"id": item["id"], "path": source, "text": item.get("text"),
              "metadata": item.get("metadata", {}), "source_id": item.get("source_id")}
    config = Config(**config_dict)
    target = Path(target)
    receipt = target.with_suffix(target.suffix + ".receipt.json")
    try:
        # Resume assumes the frozen manifest but verifies the actual source bytes.
        if sha256_file(source) != item["file_sha256"]:
            raise ValueError("Source changed after analysis")
        if resume and target.exists() and receipt.exists():
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            if saved["source_sha256"] == item["file_sha256"] and saved["output_sha256"] == sha256_file(target):
                result.update(export_status="ok", output_path=str(target), width=saved["width"], height=saved["height"])
                return result
        rgb, _ = canonical_image(source, config)
        if config.export_max_edge:
            rgb.thumbnail((config.export_max_edge, config.export_max_edge), resample=Image.Resampling.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        save_options = {"quality": config.export_jpeg_quality, "subsampling": 0} if config.export_format == "jpeg" else {}
        rgb.save(temporary, format=config.export_format.upper(), **save_options)
        temporary.replace(target)
        write_json(receipt, {"source_sha256": item["file_sha256"], "output_sha256": sha256_file(target),
                             "width": rgb.width, "height": rgb.height})
        result.update(export_status="ok", output_path=str(target), width=rgb.width, height=rgb.height)
        rgb.close()
    except Exception as exc:
        result.update(export_status="error", error=f"{type(exc).__name__}: {exc}")
    return result
