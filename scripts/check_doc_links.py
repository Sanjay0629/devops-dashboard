#!/usr/bin/env python3
"""Verify every relative Markdown link resolves to a real file and heading.

The documentation set cross-references itself heavily (docs/*.md, CLAUDE.md,
PROGRESS.md). A renamed heading silently breaks those links, so CI checks them.

Anchor slugs follow GitHub's rules: lowercase, drop punctuation except hyphen
and underscore, spaces to hyphens. Links inside fenced code blocks are ignored,
because those are examples rather than navigation.

Usage: python scripts/check_doc_links.py [root]
Exit code 1 if any link is broken.
"""

from __future__ import annotations

import os
import re
import sys

FENCE = re.compile(r"```.*?```", re.DOTALL)
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "coverage", "__pycache__"}


def slugify(heading: str) -> str:
    text = heading.strip().lower()
    text = text.replace("`", "")
    text = re.sub(r"\*\*|\*", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links in headings
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def markdown_files(root: str) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        found.extend(
            os.path.join(dirpath, name)
            for name in filenames
            if name.endswith(".md")
        )
    return sorted(found)


def anchors_of(path: str) -> set[str]:
    anchors: set[str] = set()
    in_fence = False
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = HEADING.match(line)
            if match:
                anchors.add(slugify(match.group(2)))
    return anchors


def main(root: str) -> int:
    files = markdown_files(root)
    anchors = {os.path.normpath(path): anchors_of(path) for path in files}

    problems: list[str] = []
    checked = 0

    for path in files:
        with open(path, encoding="utf-8") as handle:
            body = FENCE.sub("", handle.read())
        for _label, target in LINK.findall(body):
            if target.startswith(("http://", "https://", "mailto:", "#!")):
                continue
            checked += 1
            file_part, _, anchor = target.partition("#")
            resolved = (
                os.path.normpath(path)
                if not file_part
                else os.path.normpath(os.path.join(os.path.dirname(path), file_part))
            )
            relative = os.path.relpath(path, root)
            if not os.path.isfile(resolved):
                problems.append(f"missing file   {relative} -> {target}")
            elif anchor and anchor not in anchors.get(resolved, set()):
                problems.append(f"missing anchor {relative} -> {target}")

    for problem in problems:
        print(problem)
    print(f"\nchecked {checked} relative links in {len(files)} files; {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
