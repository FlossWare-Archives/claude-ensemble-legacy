"""Every relative Markdown link in README.md must resolve inside the repository."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]


def test_readme_relative_markdown_links_resolve() -> None:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    links = re.findall(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", text)
    missing: list[str] = []
    for target in links:
        target = target.strip().split()[0].strip("<>")
        if not target or target.startswith(("https://", "http://", "mailto:", "#", "data:")):
            continue
        path = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not path:
            continue
        resolved = (readme.parent / path).resolve()
        if not resolved.exists() or not resolved.is_relative_to(ROOT.resolve()):
            missing.append(target)
    assert not missing, "Broken README relative links: " + ", ".join(missing)
