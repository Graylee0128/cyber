"""Flag comparison for `submission`-type objectives (#33 WS5-4).

The expected value is **environment-rotated**, never scenario-file content
and never hardcoded (WS2 spec §5.1 / #45): a static flag in a version-
controlled scenario file survives across cohorts once one player shares it.
`range-up` mints a fresh flag every run and writes it to a shared file
(`scripts/range/build-vm-target.sh`, `scripts/range/flag_mint.py`); this
module only reads that file, never caching, so a second `range-up` run
invalidates the previous flag on the very next comparison with no restart.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

#: Mirrors `scripts/range/flag_mint.py:FLAG_PATTERN` exactly. Duplicated
#: rather than imported: `scripts/range/` is not an importable package
#: (it is a directory of standalone CLI scripts invoked by shell), and
#: relocating flag_mint.py would touch build-vm-target.sh and WS6's golden
#: fingerprinting for no functional gain. `tests/range_core/test_flags.py`
#: loads flag_mint.py by path and asserts the two patterns stay identical.
FLAG_PATTERN = re.compile(r"^flag\{[0-9a-f]{32}\}$")

#: Matches `scripts/range/build-vm-target.sh:FLAG_SHARE_FILE` default.
DEFAULT_FLAG_FILE = "/var/lib/purplescope/current-flag.txt"


class FlagUnavailable(RuntimeError):
    """The shared flag file is missing, unreadable, or empty.

    Must never be papered over as accept-all or reject-all — a submission
    endpoint that swallows this exception either lets every string through
    or rejects every string, and both look like normal gameplay from the
    outside instead of an infrastructure fault.
    """


class FlagSource(Protocol):
    def current(self) -> str: ...


@dataclass(frozen=True)
class SharedFileFlagSource:
    """Reads the `range-up`-written shared file on every call — no caching.

    One flag file covers the whole environment in v1 (`build-vm-target.sh`
    writes exactly one path); a scenario with multiple `submission`
    objectives therefore shares one expected value. Per-objective flags are
    a #45 follow-up (keyed file written by range-up), not built here: the
    ambiguity is real today, but the call site (`ObjectiveStore.complete_by_submission`)
    already threads `objective_id` through, so upgrading later does not
    change any caller's signature.
    """

    path: Path | str = DEFAULT_FLAG_FILE

    def current(self) -> str:
        path = Path(self.path)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FlagUnavailable(f"{path}: flag share file unreadable: {exc}") from exc
        value = raw.strip()
        if not value:
            raise FlagUnavailable(f"{path}: flag share file is empty")
        return value


@dataclass(frozen=True)
class FixtureFlagSource:
    """Test double — an in-memory flag, no filesystem I/O."""

    value: str

    def current(self) -> str:
        return self.value


def is_valid_flag_shape(value: str) -> bool:
    """Format guard only — never the sole gate. A scenario-file-shaped
    string (e.g. copy-pasted from a briefing) must still fail the *value*
    comparison even when it happens to match this shape."""
    return bool(FLAG_PATTERN.match(value))


def matches(submitted: str, source: FlagSource) -> bool:
    """Constant-time comparison against the current rotated flag.

    Raises `FlagUnavailable` (never swallowed here) when the source can't
    produce a current value — the caller decides how to surface that (503,
    per ADR/plan), not this function.
    """
    expected = source.current()
    return hmac.compare_digest(submitted, expected)
