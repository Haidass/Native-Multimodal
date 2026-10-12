import argparse
import json
import os
from pathlib import Path
import sys
import sqlite3
from dataclasses import replace

from .config import load_config
from .io import prepare
from .jobs import analyze, export_images, merge


def add_aspect_arguments(p):
    p.add_argument("--aspect-buckets", help="Width:height buckets, e.g. 1:1,2:3,3:2; all selects eight presets; none disables")
    p.add_argument("--aspect-mode", choices=["strict", "loose"], help="Exact ratio or approximate ratio (default: strict)")
    p.add_argument("--aspect-tolerance", type=float, help="Symmetric relative deviation, inclusive; default 0.05 (5%%), loose mode only")


def parser():
    p = argparse.ArgumentParser(description="Batch image-text curation: inspect, shard, globally deduplicate, export")
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="Freeze a recursive folder or JSONL input into a manifest")
    prep.add_argument("--input", required=True)
    prep.add_argument("--manifest", required=True)
    prep.add_argument("--root", help="Base directory for relative image paths")
    prep.add_argument("--sidecar-text", action="store_true", help="Read sibling UTF-8 .txt captions")
    inspection = sub.add_parser("analyze", help="Analyze one deterministic rank, resume using SQLite checkpoints")
    inspection.add_argument("--manifest", required=True)
    inspection.add_argument("--output", required=True)
    inspection.add_argument("--config")
    inspection.add_argument("--resolution", help="Keep short edge >=256 (256p), exact size (256x256), or any")
    inspection.add_argument("--root", help="Override dataset root on this node")
    inspection.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    inspection.add_argument("--rank", type=int, default=0)
    inspection.add_argument("--world-size", type=int, default=1)
    inspection.add_argument("--resume", action="store_true")
    combined = sub.add_parser("merge", aliases=["filter"], help="Re-filter all analyzed shards, globally deduplicate and report")
    combined.add_argument("--shards", required=True)
    combined.add_argument("--output", required=True)
    combined.add_argument("--config", help="Optional new filtering thresholds; preprocessing must remain identical")
    combined.add_argument("--resolution", help="Keep short edge >=256 (256p), exact size (256x256), or any; overrides config")
    add_aspect_arguments(inspection)
    add_aspect_arguments(combined)
    export = sub.add_parser("export", help="Write normalized retained images to a separate directory")
    export.add_argument("--curated", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--root", help="Override root if images have moved to another node")
    export.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    export.add_argument("--resume", action="store_true")
    run = sub.add_parser("run", help="Single-machine prepare + multiprocess analyze + global merge")
    run.add_argument("--input", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--root")
    run.add_argument("--config")
    run.add_argument("--resolution", help="Keep short edge >=256 (256p), exact size (256x256), or any; overrides config")
    run.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    run.add_argument("--sidecar-text", action="store_true")
    run.add_argument("--export", action="store_true")
    run.add_argument("--resume", action="store_true")
    add_aspect_arguments(run)
    return p


def cli_config(args):
    config = load_config(args.config)
    return replace(config, **cli_overrides(args))


def cli_overrides(args):
    return {name: getattr(args, name) for name in
            ("resolution", "aspect_buckets", "aspect_mode", "aspect_tolerance")
            if getattr(args, name) is not None}


def dispatch(args):
    if args.command == "prepare":
        return prepare(args.input, args.manifest, args.root, args.sidecar_text)
    if args.command == "analyze":
        return analyze(args.manifest, args.output, cli_config(args), args.workers,
                       args.rank, args.world_size, args.root, args.resume)
    if args.command in {"merge", "filter"}:
        return merge(args.shards, args.output, load_config(args.config) if args.config else None, **cli_overrides(args))
    if args.command == "export":
        return export_images(args.curated, args.output, args.workers, args.root, args.resume)
    output = Path(args.output).resolve()
    input_path = Path(args.input).resolve()
    if input_path.is_dir() and output.is_relative_to(input_path):
        raise ValueError("Output must be outside the source image directory")
    config = cli_config(args)
    manifest = output/"input.jsonl"
    run_identity = {"input": str(input_path), "config": config.fingerprint(),
                    "root": str(Path(args.root).resolve()) if args.root else None,
                    "sidecar_text": args.sidecar_text}
    from .io import write_json, exclusive_lock
    with exclusive_lock(output/"run.lock"):
        identity_path = output/"run.meta.json"
        if identity_path.exists():
            existing = json.loads(identity_path.read_text(encoding="utf-8"))
            if not args.resume or existing != run_identity:
                raise ValueError("Run exists or identity changed; use --resume with the same input/config")
        else:
            write_json(identity_path, run_identity)
        if not manifest.exists():
            prepare(args.input, manifest, args.root, args.sidecar_text)
        analyze(manifest, output/"shards", config, args.workers, root=args.root, resume=args.resume)
        if (output/"curated"/"curation.sqlite").exists():
            if not args.resume:
                raise ValueError("Run already merged; use --resume")
            result = json.loads((output/"curated"/"report.json").read_text(encoding="utf-8"))
            result = {k: result[k] for k in ["input_count", "kept_count", "rejected_count", "duplicate_count", "error_count"]}
        else:
            result = merge(output/"shards", output/"curated")
        if args.export:
            result["export"] = export_images(output/"curated", output/"normalized", args.workers, args.root, args.resume)
        return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "export" and result.get("error", 0):
        return 1
    if args.command == "run" and result.get("export", {}).get("error", 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
