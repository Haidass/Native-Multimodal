"""Validate built local links, documented Config coverage, and source ZIP contents."""
from dataclasses import fields
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from image_cleaner.config import Config


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.ids, self.links = set(), []
        self.feed(path.read_text(encoding="utf-8"))

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.add(values["id"])
        if tag in {"a", "link", "script"}:
            value = values.get("href", values.get("src"))
            if value:
                self.links.append(value)


def main():
    pages = [ROOT/"index.html"] + sorted((ROOT/"manual").glob("*.html"))
    parsed = {path.resolve(): Page(path) for path in pages}
    checked = 0
    for path, page in parsed.items():
        for link in page.links:
            target = urlsplit(link)
            if target.scheme or target.netloc:
                continue
            resolved = (path.parent/unquote(target.path)).resolve() if target.path else path
            if not resolved.is_relative_to(ROOT) or not resolved.is_file():
                raise ValueError(f"Broken local link: {path.name} -> {link}")
            if target.fragment and resolved.suffix == ".html":
                if unquote(target.fragment) not in parsed[resolved].ids:
                    raise ValueError(f"Broken anchor: {path.name} -> {link}")
            checked += 1
    documented = set(re.findall(r"^\| ([a-z_]+) \|", (ROOT/"docs/PARAMETERS.md").read_text(encoding="utf-8"), re.M))
    missing = {field.name for field in fields(Config)} - documented
    if missing:
        raise ValueError(f"Undocumented Config fields: {sorted(missing)}")
    manifest = json.loads((ROOT/"release/publish-manifest.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(ROOT/manifest["archive"]) as archive:
        if archive.testzip() is not None:
            raise ValueError("Corrupt source archive")
        if any(part in {".venv", "runs", "release", "__pycache__"} for name in archive.namelist() for part in Path(name).parts):
            raise ValueError("Excluded local artifacts were found in archive")
        source_count = len(archive.namelist())
    print(json.dumps({"html_pages": len(pages), "local_links_verified": checked,
                      "config_fields_documented": len(fields(Config)), "source_archive_files": source_count}))


if __name__ == "__main__":
    main()
