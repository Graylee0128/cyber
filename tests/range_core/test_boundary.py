"""Cyber Range Core (Z-APP) must never import Purple Platform (Z-MGMT), #33.

`scenarios.py`'s module docstring has claimed this since #31; nothing
enforced it. #33 is exactly the ticket where a scoring module would be
tempted to reach into `purple.metrics` for a shortcut, so the boundary
becomes a mechanical AST check instead of a claim in a docstring.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RANGE_CORE_ROOT = REPO_ROOT / "src" / "range_core"


def _imported_top_level_modules(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_range_core_never_imports_purple():
    offenders = {}
    for path in RANGE_CORE_ROOT.rglob("*.py"):
        modules = _imported_top_level_modules(path)
        if "purple" in modules:
            offenders[str(path.relative_to(REPO_ROOT))] = "purple"
    assert offenders == {}, (
        f"src/range_core/** must not import purple (Z-APP/Z-MGMT boundary): {offenders}"
    )
