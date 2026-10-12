"""Publish an audited source/documentation whitelist using a process-only HF_TOKEN."""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from huggingface_hub import HfApi, CommitOperationAdd
from huggingface_hub.errors import RepositoryNotFoundError
from build_space import ROOT, check_path, publish_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", default="HanWangTJU/NMM-data-preprocess")
    args = parser.parse_args()
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise ValueError("Set HF_TOKEN in the current process environment before publishing")
    manifest = json.loads((ROOT/"release/publish-manifest.json").read_text(encoding="utf-8"))
    expected = {path.relative_to(ROOT).as_posix() for path in publish_files()}
    actual = [entry["path"] for entry in manifest["files"]]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError("Manifest differs from the publication whitelist; rebuild first")
    operations = []
    for entry in manifest["files"]:
        path = ROOT/entry["path"]
        check_path(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("A release file changed after build; rebuild before publishing")
        operations.append(CommitOperationAdd(path_in_repo=entry["path"], path_or_fileobj=str(path)))
    api = HfApi(token=token)
    user = api.whoami()["name"]
    if args.repo_id.split("/")[0] != user:
        raise ValueError("Authenticated account does not match the requested Space namespace")
    try:
        info = api.repo_info(args.repo_id, repo_type="space")
        if info.sdk != "static":
            raise ValueError("Existing Space is not static; refusing to replace its SDK")
        parent_commit = info.sha
    except RepositoryNotFoundError as exc:
        if getattr(exc.response, "status_code", None) != 404:
            raise
        api.create_repo(args.repo_id, repo_type="space", space_sdk="static", private=False)
        parent_commit = api.repo_info(args.repo_id, repo_type="space").sha
    commit = api.create_commit(repo_id=args.repo_id, repo_type="space", operations=operations,
                               parent_commit=parent_commit,
                               commit_message=f"Publish NMM data preprocessing v{manifest['version']} with Chinese tutorials")
    info = api.repo_info(args.repo_id, repo_type="space", revision=commit.oid, files_metadata=True)
    remote = {item.rfilename: item for item in info.siblings}
    if not expected.issubset(remote):
        raise ValueError("Commit completed but a published path is missing; inspect repository")
    if any(remote[item["path"]].size != item["bytes"] for item in manifest["files"]):
        raise ValueError("Committed file sizes do not match release manifest")
    receipt = {"repo_id": args.repo_id, "version": manifest["version"], "commit": commit.oid,
               "space_url": f"https://huggingface.co/spaces/{args.repo_id}",
               "published_file_count": len(operations), "verified_remote_sizes": True,
               "published_at_utc": datetime.now(timezone.utc).isoformat()}
    (ROOT/"release/publish-receipt.json").write_text(json.dumps(receipt, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Do not print HTTP request objects or environment values on authentication failures.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "http_status": status,
                          "reason": str(exc) if isinstance(exc, ValueError) else "Hugging Face request failed"}), file=sys.stderr)
        sys.exit(2)
