"""#33 WS5-4: flag comparison reads a range-up-rotated shared file, never
scenario content, never a hardcoded value."""

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

import pytest

from range_core.flags import (
    FLAG_PATTERN,
    FixtureFlagSource,
    FlagUnavailable,
    SharedFileFlagSource,
    is_valid_flag_shape,
    matches,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_correct_flag_from_shared_file_matches():
    flag_file = "flag{" + "a" * 32 + "}"
    source = FixtureFlagSource(flag_file)

    assert matches(flag_file, source) is True
    assert matches("flag{" + "b" * 32 + "}", source) is False


def test_scenario_file_shaped_string_does_not_validate_by_shape_alone():
    """A scenario-shaped fixture flag must not become the accepted answer
    just because it *looks* like a flag — only the current rotated value
    from the shared file counts."""
    fixture_looking = "flag{" + "c" * 32 + "}"
    assert is_valid_flag_shape(fixture_looking) is True

    source = FixtureFlagSource("flag{" + "d" * 32 + "}")
    assert matches(fixture_looking, source) is False


def test_rotation_second_range_up_invalidates_previous_flag(tmp_path):
    flag_path = tmp_path / "current-flag.txt"
    flag_a = "flag{" + "1" * 32 + "}"
    flag_b = "flag{" + "2" * 32 + "}"
    flag_path.write_text(flag_a + "\n", encoding="utf-8")
    source = SharedFileFlagSource(flag_path)

    assert matches(flag_a, source) is True

    flag_path.write_text(flag_b + "\n", encoding="utf-8")

    assert matches(flag_a, source) is False
    assert matches(flag_b, source) is True


def test_missing_file_raises_flag_unavailable(tmp_path):
    source = SharedFileFlagSource(tmp_path / "does-not-exist.txt")

    with pytest.raises(FlagUnavailable):
        source.current()


def test_unreadable_file_raises_flag_unavailable(tmp_path):
    flag_path = tmp_path / "current-flag.txt"
    flag_path.write_text("flag{" + "3" * 32 + "}\n", encoding="utf-8")
    source = SharedFileFlagSource(flag_path)

    if sys.platform == "win32":
        pytest.skip("POSIX permission bits don't apply on Windows runners")
    flag_path.chmod(0)
    try:
        with pytest.raises(FlagUnavailable):
            source.current()
    finally:
        flag_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_empty_file_raises_flag_unavailable(tmp_path):
    flag_path = tmp_path / "current-flag.txt"
    flag_path.write_text("", encoding="utf-8")
    source = SharedFileFlagSource(flag_path)

    with pytest.raises(FlagUnavailable):
        source.current()


def test_reads_are_not_cached_across_instances_or_calls(tmp_path):
    flag_path = tmp_path / "current-flag.txt"
    flag_path.write_text("flag{" + "4" * 32 + "}\n", encoding="utf-8")
    source = SharedFileFlagSource(flag_path)
    first = source.current()

    flag_path.write_text("flag{" + "5" * 32 + "}\n", encoding="utf-8")
    second = source.current()

    assert first != second


def test_flag_pattern_matches_flag_mint_script():
    """Contract test: `FLAG_PATTERN` here must stay byte-identical to
    `scripts/range/flag_mint.py`'s pattern — that script is not an
    importable package, so this loads it by path instead."""
    script_path = REPO_ROOT / "scripts" / "range" / "flag_mint.py"
    spec = importlib.util.spec_from_file_location("flag_mint", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert FLAG_PATTERN.pattern == module.FLAG_PATTERN.pattern
    minted = module.mint_flag()
    assert is_valid_flag_shape(minted)
