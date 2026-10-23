"""The Jenkins job path grammar (``OPEN-120``).

A target stores a slash-separated job path such as ``team/service-deploy``. DCP never puts that
string into a URL as written: :func:`job_url_path` builds ``/job/<segment>/job/<segment>`` from
the validated segments. A segment cannot start with ``.``, so ``.`` and ``..`` are inexpressible,
and there is no ``%``, ``?``, ``#`` or whitespace to smuggle a second path or a query.

The same pattern is a database ``CHECK`` (migration ``0014``): a direct ``UPDATE`` meets it too.
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import quote

JOB_SEGMENT: Final = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
#: Used verbatim by the migration's ``CHECK`` and by ``fullmatch`` here.
JOB_PATH_PATTERN: Final = rf"^{JOB_SEGMENT}(/{JOB_SEGMENT}){{0,4}}$"
_JOB_PATH: Final = re.compile(rf"{JOB_SEGMENT}(/{JOB_SEGMENT}){{0,4}}")


class InvalidJobPathError(ValueError):
    """The message names the rule, never the value."""


def validate_job_path(value: str) -> str:
    """Return ``value`` if it is a job path DCP may use, else :class:`InvalidJobPathError`."""
    if not isinstance(value, str) or _JOB_PATH.fullmatch(value) is None:
        raise InvalidJobPathError(
            "must be 1 to 5 segments separated by '/', each starting with a letter or digit "
            "and holding only letters, digits, '.', '_' and '-' (at most 64 characters each)"
        )
    return value


def job_url_path(job_path: str) -> str:
    """``team/svc`` -> ``/job/team/job/svc``, built from validated segments only."""
    validate_job_path(job_path)
    return "".join(f"/job/{quote(segment, safe='')}" for segment in job_path.split("/"))
