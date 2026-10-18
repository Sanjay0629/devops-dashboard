"""Write (or, with ``--check``, verify) ``infra/prometheus/rules/dcp-alerts.yml``.

Run from ``backend/`` so the application package imports::

    cd backend && uv run python ../scripts/gen_alert_rules.py            # write
    cd backend && uv run python ../scripts/gen_alert_rules.py --check    # CI / check-all

The numbers come from the same two environment variables the application reads,
``DCP_UTILISATION_THRESHOLDS`` and ``DCP_HEALTH_THRESHOLDS`` (empty means the shipped
defaults, which is what the committed file is generated from). ``--output`` writes
somewhere else, which is how the smoke test builds a file with its own, faster
thresholds without touching the committed one (OPEN-46).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "infra/prometheus/rules/dcp-alerts.yml"


def _table(variable: str, model):  # type: ignore[no-untyped-def]
    raw = os.environ.get(variable, "").strip()
    return model.model_validate(json.loads(raw) if raw else {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the file is not current")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        from app.alerts import rulegen
        from app.monitoring.health import HealthThresholdTable
        from app.monitoring.utilisation import UtilisationThresholdTable
    except ImportError as error:
        print(f"Run this from backend/: cd backend && uv run python ../scripts/gen_alert_rules.py\n({error})")
        return 2

    text = rulegen.render(
        _table("DCP_UTILISATION_THRESHOLDS", UtilisationThresholdTable),
        _table("DCP_HEALTH_THRESHOLDS", HealthThresholdTable),
    )
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else None
        if current != text:
            print(f"{args.output} is stale. Regenerate it:\n  cd backend && uv run python ../scripts/gen_alert_rules.py")
            return 1
        print(f"{args.output} is current")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
