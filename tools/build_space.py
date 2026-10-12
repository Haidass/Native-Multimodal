"""Build an offline static tutorial and a source-only release archive."""
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import zipfile

import markdown
from markdown.extensions.toc import slugify_unicode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from image_cleaner import __version__

REPO = "HanWangTJU/NMM-data-preprocess"
DOCS = {"QUICKSTART": "逐步教程", "USAGE": "完整手册", "PARAMETERS": "参数参考",
        "DEPLOYMENT": "部署指南", "ARCHITECTURE": "代码说明"}
SOURCE_DIRS = ("image_cleaner", "configs", "docs", "examples", "tests", "tools", "web", "docker")
ROOT_FILES = ("README.md", "pyproject.toml", ".gitignore", ".dockerignore")
ALLOWED_SUFFIXES = {".py", ".json", ".md", ".html", ".css", ".js"}


def source_files():
    paths = [ROOT/name for name in ROOT_FILES]
    for directory in SOURCE_DIRS:
        for path in (ROOT/directory).rglob("*"):
            if path.is_file() and (path.suffix in ALLOWED_SUFFIXES or path.name == "Dockerfile"):
                if "__pycache__" not in path.parts:
                    paths.append(path)
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("Publish source must remain inside the project")
    return sorted(set(paths))


def check_content(content):
    if re.search(rb"hf_[A-Za-z0-9]{20,}", content):
        raise ValueError("A credential-like Hugging Face token was detected; publication stopped")
    if re.search(rb"(?i)(?:password|passwd)\s*[:=]\s*['\"][^'\"]{4,}['\"]", content):
        raise ValueError("A password assignment was detected; publication stopped")


def check_path(path):
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
        raise ValueError("Publish path must remain inside the project")
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            expected = {p.relative_to(ROOT).as_posix() for p in source_files()}
            if set(archive.namelist()) != expected:
                raise ValueError("Archive contents differ from source whitelist")
            for name in archive.namelist():
                check_content(archive.read(name))
    else:
        check_content(path.read_bytes())


def publish_files():
    return source_files() + [ROOT/"index.html", ROOT/"assets/style.css", ROOT/"assets/app.js"] + [
        ROOT/"manual"/(name + ".html") for name in DOCS
    ] + [ROOT/"downloads"/f"NMM-data-preprocess-{__version__}.zip"]


def rewrite_links(body):
    def rewrite(match):
        target = html.unescape(match.group(1))
        base, _, fragment = target.partition("#")
        if base.endswith(".md"):
            stem = Path(base).stem
            if stem in DOCS:
                return 'href="' + stem + ".html" + ("#"+fragment if fragment else "") + '"'
            return 'href="https://huggingface.co/spaces/' + REPO + '/blob/main/' + html.escape(base) + '"'
        return match.group(0)
    return re.sub(r'href="([^"]+)"', rewrite, body)


def build():
    for directory in ["manual", "assets", "downloads", "release"]:
        (ROOT/directory).mkdir(exist_ok=True)
    # ASCII entities prevent the static host's script injection from splitting
    # multibyte UTF-8 characters across its byte-buffer boundaries.
    homepage = (ROOT/"web/index.template.html").read_text(encoding="utf-8").replace("@@VERSION@@", __version__)
    (ROOT/"index.html").write_bytes(homepage.encode("ascii", errors="xmlcharrefreplace"))
    for filename in ["style.css", "app.js"]:
        (ROOT/"assets"/filename).write_bytes((ROOT/"web"/filename).read_bytes())
    for name, title in DOCS.items():
        renderer = markdown.Markdown(extensions=["toc", "tables", "fenced_code", "sane_lists"],
                                     extension_configs={"toc": {"toc_depth": "2-3", "slugify": slugify_unicode}})
        body = rewrite_links(renderer.convert((ROOT/"docs"/(name+".md")).read_text(encoding="utf-8")))
        nav = "".join(f'<a class="{"active" if key == name else ""}" href="{key}.html">{label}</a>'
                      for key, label in DOCS.items())
        page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{title} · NMM-data-preprocess</title><link rel="stylesheet" href="../assets/style.css">'
                '<script defer src="../assets/app.js"></script></head><body><header class="topbar">'
                '<a class="brand" href="../index.html">NMM<span> / </span>data-preprocess</a>'
                f'<nav><a href="https://huggingface.co/spaces/{REPO}/tree/main">源代码 ↗</a>'
                f'<a href="../downloads/NMM-data-preprocess-{__version__}.zip" download>下载源码 ↓</a></nav></header>'
                f'<div class="manual-layout"><aside class="sidebar"><a href="../index.html">← 返回首页</a>{nav}'
                f'<h2>本页目录</h2>{renderer.toc}</aside><main class="manual">{body}</main></div>'
                f'<footer>NMM-data-preprocess · v{__version__}<span>图片文本数据初筛教程</span></footer></body></html>')
        (ROOT/"manual"/(name+".html")).write_bytes(page.encode("ascii", errors="xmlcharrefreplace"))
    archive_path = ROOT/"downloads"/f"NMM-data-preprocess-{__version__}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source_files():
            check_path(path)
            # Fixed metadata makes the archive reproducible across local runs.
            entry = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, path.read_bytes())
    files = []
    for path in publish_files():
        check_path(path)
        data = path.read_bytes()
        files.append({"path": path.relative_to(ROOT).as_posix(), "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {"repo_id": REPO, "version": __version__, "files": files,
                "archive": archive_path.relative_to(ROOT).as_posix()}
    (ROOT/"release/publish-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"version": __version__, "publish_files": len(files),
                      "publish_bytes": sum(item["bytes"] for item in files), "archive": manifest["archive"]}))


if __name__ == "__main__":
    build()
