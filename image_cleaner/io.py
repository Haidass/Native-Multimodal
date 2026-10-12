from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif"}


def json_line(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def atomic_text(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    with atomic_text(path) as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
        handle.write("\n")


def walk_images(root):
    # Stable traversal independent of filesystem enumeration order; directory lists only.
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(directory)/d).is_symlink())
        for name in sorted(files):
            path = Path(directory)/name
            if path.suffix.lower() in EXTENSIONS:
                yield path


def prepare(input_path, manifest, root=None, sidecar_text=False):
    input_path, manifest = Path(input_path).resolve(), Path(manifest).resolve()
    if not input_path.exists():
        raise ValueError(f"Input does not exist: {input_path}")
    if input_path == manifest:
        raise ValueError("Input and output manifest must differ")
    if manifest.exists():
        raise ValueError(f"Manifest already exists: {manifest}; choose a new path")
    base = Path(root).resolve() if root else (input_path if input_path.is_dir() else input_path.parent)
    # Output inside the input tree can be accidentally re-imported on subsequent runs.
    if input_path.is_dir() and manifest.is_relative_to(input_path):
        raise ValueError("Place the manifest outside the input image directory")
    count = 0
    if input_path.is_dir():
        def entries():
            for path in walk_images(input_path):
                try:
                    relative = path.relative_to(base).as_posix()
                except ValueError as exc:
                    raise ValueError("All discovered images must be beneath --root") from exc
                item = {"path": relative}
                txt = path.with_suffix(".txt")
                if sidecar_text and txt.exists():
                    item["text"] = txt.read_text(encoding="utf-8-sig")
                yield item
    else:
        def entries():
            with input_path.open(encoding="utf-8-sig") as handle:
                for line_no, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Invalid JSON at input line {line_no}") from exc
                    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                        raise ValueError(f"Input line {line_no} requires a string path")
                    if item.get("text") is not None and not isinstance(item["text"], str):
                        raise ValueError(f"Input line {line_no}: text must be string/null")
                    if "metadata" in item and not isinstance(item["metadata"], dict):
                        raise ValueError(f"Input line {line_no}: metadata must be an object")
                    # Preserve caption identity and arbitrary fields inside metadata to avoid
                    # collisions with computed metrics (e.g. an input field named width).
                    known = {"path", "text", "id", "metadata"}
                    metadata = dict(item.get("metadata", {}))
                    metadata.update({k: v for k, v in item.items() if k not in known})
                    yield {"path": item["path"], "text": item.get("text"),
                           "source_id": item.get("id"), "metadata": metadata}
    with atomic_text(manifest) as handle:
        for item in entries():
            item["seq"] = count
            item["id"] = f"sample-{count:012d}"
            handle.write(json_line(item))
            count += 1
    if count == 0:
        manifest.unlink()
        raise ValueError("No images or manifest records found")
    metadata = {"schema_version": 1, "count": count, "root": str(base),
                "sha256": sha256_file(manifest)}
    write_json(str(manifest) + ".meta.json", metadata)
    return metadata


def manifest_info(manifest, root=None):
    manifest = Path(manifest).resolve()
    metadata = json.loads(Path(str(manifest) + ".meta.json").read_text(encoding="utf-8"))
    if metadata["sha256"] != sha256_file(manifest):
        raise ValueError("Manifest has changed after prepare; prepare a new manifest")
    base = Path(root or metadata["root"]).resolve()
    return metadata, base


def manifest_items(manifest):
    with Path(manifest).open(encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def resolve_source(base, item):
    path = Path(item["path"])
    return path if path.is_absolute() else base/path


@contextmanager
def exclusive_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError(f"Job is locked: {path}. If no job is running, remove its stale lock.") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def connect(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA cache_size=-32768")  # 32 MiB bounded page cache.
    db.execute("PRAGMA temp_store=FILE")
    return db


def get_meta(db, key):
    row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def set_meta(db, key, value):
    db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value)))


def parallel_bounded(function, iterable, workers):
    """At most 2*workers futures in memory, unlike eager Executor.map in Python <=3.13."""
    if workers == 1:
        yield from map(function, iterable)
        return
    iterator = iter(iterable)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = set()
        exhausted = False
        while pending or not exhausted:
            while not exhausted and len(pending) < 2 * workers:
                try:
                    pending.add(pool.submit(function, next(iterator)))
                except StopIteration:
                    exhausted = True
            if pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    yield future.result()
