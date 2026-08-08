"""Technique 白名單 —— 純函數，欄位治理的核心（ADR ⑤）。

白名單外的 technique 進不來。Grafana rule label 與紅隊動作註冊清單共用這一份，
所以 coverage 表兩邊對得起來。層級不一致（父＋子技術並存）在載入時就擋下。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

#: 白名單檔位置，相對 repo root 解析（不受 cwd 影響）。
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "techniques.yaml"


class WhitelistError(Exception):
    """白名單檔本身有問題（層級不一致、欄位缺漏）。載入時就要吵。"""


class TechniqueRejected(Exception):
    """某個 technique 不在白名單內。由 receiver 記錄並拒收，不得靜默通過。"""


@dataclass(frozen=True)
class Technique:
    id: str
    name: str
    tactic: str
    note: str | None = None


@dataclass(frozen=True)
class Whitelist:
    techniques: tuple[Technique, ...]

    def allows(self, technique_id: str) -> bool:
        return any(t.id == technique_id for t in self.techniques)

    def note(self, technique_id: str) -> str | None:
        for t in self.techniques:
            if t.id == technique_id:
                return t.note
        return None

    def ids(self) -> frozenset[str]:
        return frozenset(t.id for t in self.techniques)


def parse_whitelist(raw: dict) -> Whitelist:
    entries = raw.get("techniques") or []
    if not entries:
        raise WhitelistError("白名單是空的 —— 沒有任何 technique 會被接受")

    techniques: list[Technique] = []
    seen: set[str] = set()
    for e in entries:
        tid = e.get("id")
        if not tid:
            raise WhitelistError("有一筆 technique 缺少 id")
        if tid in seen:
            raise WhitelistError(f"technique {tid!r} 重複出現")
        seen.add(tid)
        techniques.append(
            Technique(id=tid, name=e.get("name", ""), tactic=e.get("tactic", ""), note=e.get("note"))
        )

    _reject_mixed_levels(seen)
    return Whitelist(techniques=tuple(techniques))


def _reject_mixed_levels(ids: set[str]) -> None:
    """父技術與其子技術不可同時出現（T1190 與 T1190.001）—— 否則 coverage 重複計數。"""
    for tid in ids:
        if "." in tid:
            parent = tid.split(".", 1)[0]
            if parent in ids:
                raise WhitelistError(
                    f"層級不一致：{parent!r} 與其子技術 {tid!r} 同時在白名單內，"
                    f"coverage 會重複計數"
                )


def load_whitelist(path: Path = DEFAULT_PATH) -> Whitelist:
    if not path.exists():
        raise WhitelistError(f"找不到白名單檔：{path}")
    return parse_whitelist(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


@lru_cache(maxsize=1)
def default_whitelist() -> Whitelist:
    """預設白名單，載入一次後快取。"""
    return load_whitelist()


def check_technique(technique_id: str, whitelist: Whitelist | None = None) -> None:
    """白名單外就拋 TechniqueRejected。呼叫端負責記錄，不得靜默吞掉。"""
    wl = whitelist or default_whitelist()
    if not wl.allows(technique_id):
        raise TechniqueRejected(
            f"technique {technique_id!r} 不在白名單內（{sorted(wl.ids())}）"
        )
