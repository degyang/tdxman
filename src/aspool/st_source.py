"""Conservative same-day ST status classification from quote security names."""

from __future__ import annotations

import re


# Risk-warning labels are prefixes in the A-share security name.  Anchoring the
# expression avoids treating an unrelated name containing "ST" as a risk label.
_ST_PREFIX = re.compile(r"^(?:\*?ST|S\*ST|SST)(?=\s|$|[A-Z0-9\u4e00-\u9fff])", re.IGNORECASE)


def classify_st_name(name: object) -> bool | None:
    """Return ``True``/``False`` for a usable name, otherwise ``None``.

    ``None`` is deliberately distinct from ``False``: a missing, non-text, or
    blank quote name is not evidence that the security is not ST.
    """
    if not isinstance(name, str):
        return None
    value = name.strip()
    if not value:
        return None
    return bool(_ST_PREFIX.match(value))
