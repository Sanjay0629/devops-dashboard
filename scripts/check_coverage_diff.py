#!/usr/bin/env python
"""Coverage floor on **changed** lines (OPEN-22, docs/TESTING.md §19).

§19 says coverage is a signal, not a target, and sets the floor on *changed
code* rather than on the whole tree. That distinction is the whole point: a
repository-wide percentage can sit comfortably above any threshold while the
branch under review adds an untested authorisation path, and a total-coverage
gate would pass it.

OPEN-22's working default is 80 % on changed code, gating per-diff. This
implements exactly that and nothing more.

    python scripts/check_coverage_diff.py \\
        --coverage backend/coverage.xml --base origin/main --label backend

Why a script rather than a tool: nothing in the installed set does this.
`pytest-cov --cov-fail-under` is a *total*, which is the thing §19 explicitly
declines to gate on, and `diff-cover` would be a new dependency for roughly
eighty lines of XML and `git diff` parsing (docs/TECH_STACK.md §10 rung 7).
One script also means the backend and the frontend are held to one rule, since
both emit Cobertura.

Reports a pass when the diff touches no measurable line. That is not a loophole
being left open: a diff of documentation, YAML or test files has no application
lines to cover, and failing it would make the gate noise that people learn to
override.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: `@@ -old,count +new,count @@`. With --unified=0 each hunk is exactly the
#: changed run, so the new-side range is the set of added or modified lines.
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

#: Only these are measured. A changed `.md`, `.yml` or lockfile has nothing to
#: cover, and a changed test file is the *evidence*, not the subject.
MEASURED_SUFFIXES = (".py", ".ts", ".tsx")
EXCLUDED_PARTS = ("tests", "e2e", "alembic", "node_modules")
EXCLUDED_NAMES = re.compile(r"\.(test|spec)\.(ts|tsx)$|^conftest\.py$|^test_.*\.py$")


def git(*args: str) -> str:
    # UTF-8, stated: `text=True` alone decodes with the locale's code page, which
    # on Windows is cp1252 and cannot decode some bytes of a perfectly good UTF-8
    # diff. That killed the reader thread, left stdout as None, and the gate died
    # with an AttributeError instead of a verdict (PROGRESS issue 65). `replace`
    # because only line *numbers* are read from the diff, never its text.
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        print(f"git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return result.stdout


def is_measured(path: str) -> bool:
    if not path.endswith(MEASURED_SUFFIXES):
        return False
    parts = Path(path).parts
    if any(part in EXCLUDED_PARTS for part in parts):
        return False
    return not EXCLUDED_NAMES.search(Path(path).name)


def untracked_lines() -> dict[str, set[int]]:
    """Every line of every measurable file git has not been told about yet.

    `git diff` cannot report an untracked file, so without this the gate
    measures **nothing at all** for a brand-new module — which is the shape
    an untested addition usually takes, and therefore the one case where the
    gate most needs to work.

    Found by M2.1, the first backend change this gate ever judged: a new
    six-file `app/metrics/` package was skipped in silence while the run
    printed a pass for the twenty-one lines of an *existing* file the same
    branch happened to touch. The floor was not lowered to accommodate it;
    the blind spot was closed.

    Staged additions already appear in `git diff` against the merge base, so
    this covers exactly the pre-`git add` case the module docstring promises
    to measure, and nothing else.
    """
    changed: dict[str, set[int]] = {}
    for line in git("ls-files", "--others", "--exclude-standard").splitlines():
        path = line.strip()
        if not path or not is_measured(path):
            continue
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except OSError:
            # Deleted between the listing and now, or unreadable. Not a
            # coverage gap; skip it rather than failing the gate on it.
            continue
        changed[path] = set(range(1, len(text.splitlines()) + 1))
    return changed


def changed_lines(base: str) -> dict[str, set[int]]:
    """New-side line numbers added or modified since the merge base.

    Against the **merge base**, so a branch that is behind `main` is not
    blamed for lines it never touched; and against the **working tree** rather
    than `HEAD`, so running this locally before committing measures the change
    you are about to push. In CI the working tree is the checkout, so the two
    are the same thing — but `base...HEAD` would have silently measured
    nothing at all for an uncommitted change, which is exactly when a
    contributor runs it.

    Untracked files are folded in by :func:`untracked_lines`, for the same
    reason and against the same failure.
    """
    merge_base = git("merge-base", base, "HEAD").strip()
    diff = git("diff", "--unified=0", "--no-color", "--diff-filter=d", merge_base)
    changed: dict[str, set[int]] = defaultdict(set)
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            candidate = line[len("+++ b/") :].strip()
            current = candidate if is_measured(candidate) else None
            continue
        if current is None:
            continue
        match = HUNK.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        # count 0 marks a pure deletion: there is no new line to cover.
        changed[current].update(range(start, start + count))
    for path, lines in untracked_lines().items():
        changed[path].update(lines)
    return dict(changed)


def coverage_by_file(report: Path) -> dict[str, dict[int, int]]:
    """`{path: {line: hits}}` from a Cobertura report.

    Paths are kept as written and matched by suffix later: pytest-cov reports
    relative to its `source` root and vitest relative to the package, so
    neither matches a repository-relative path on the nose.
    """
    tree = ElementTree.parse(report)
    files: dict[str, dict[int, int]] = {}
    for element in tree.iter("class"):
        filename = element.get("filename")
        if filename is None:
            continue
        lines = files.setdefault(filename.replace("\\", "/"), {})
        for line in element.iter("line"):
            number = line.get("number")
            hits = line.get("hits")
            if number is None or hits is None:
                continue
            # Max, not assignment: a file can appear more than once across
            # appended runs, and a line covered by any of them is covered.
            lines[int(number)] = max(lines.get(int(number), 0), int(hits))
    return files


def match_report_path(path: str, reported: dict[str, dict[int, int]]) -> dict[int, int] | None:
    """The report entry for a repository-relative path, or None if unmeasured.

    None is meaningful and is not treated as zero coverage: it means this
    report does not cover that file at all — a frontend file against the
    backend's report, most often — and judging it here would fail every diff
    that spans both ends.
    """
    normalised = path.replace("\\", "/")
    if normalised in reported:
        return reported[normalised]
    for candidate, lines in reported.items():
        if normalised.endswith("/" + candidate) or candidate.endswith("/" + normalised):
            return lines
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", action="append", required=True, type=Path)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--min", dest="minimum", type=float, default=80.0)
    parser.add_argument("--label", default="changed code")
    arguments = parser.parse_args()

    reported: dict[str, dict[int, int]] = {}
    for report in arguments.coverage:
        if not report.exists():
            print(f"coverage report not found: {report}", file=sys.stderr)
            return 2
        reported.update(coverage_by_file(report))

    changed = changed_lines(arguments.base)
    if not changed:
        print(f"{arguments.label}: no measured file changed against {arguments.base}")
        return 0

    total = 0
    covered = 0
    gaps: list[tuple[str, list[int]]] = []
    unmeasured: list[str] = []

    for path in sorted(changed):
        lines = match_report_path(path, reported)
        if lines is None:
            unmeasured.append(path)
            continue
        # Only lines the report knows about are coverable. A blank line, a
        # comment or an import inside `if TYPE_CHECKING` is not a gap.
        coverable = sorted(number for number in changed[path] if number in lines)
        if not coverable:
            continue
        missed = [number for number in coverable if lines[number] == 0]
        total += len(coverable)
        covered += len(coverable) - len(missed)
        if missed:
            gaps.append((path, missed))

    if total == 0:
        print(f"{arguments.label}: no coverable line changed against {arguments.base}")
        if unmeasured:
            print(f"  not in this report ({len(unmeasured)} file(s)): {', '.join(unmeasured[:5])}")
        return 0

    percentage = 100.0 * covered / total
    print(
        f"{arguments.label}: {covered}/{total} changed lines covered "
        f"({percentage:.1f}%), floor {arguments.minimum:.0f}%"
    )
    if unmeasured:
        print(f"  not in this report, so not judged: {', '.join(unmeasured)}")
    for path, missed in gaps:
        shown = ", ".join(str(number) for number in missed[:12])
        more = f" (+{len(missed) - 12} more)" if len(missed) > 12 else ""
        print(f"  uncovered {path}: {shown}{more}")

    if percentage + 1e-9 < arguments.minimum:
        print(
            f"\nFAIL: changed-line coverage {percentage:.1f}% is below the "
            f"{arguments.minimum:.0f}% floor (OPEN-22, docs/TESTING.md §19).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
