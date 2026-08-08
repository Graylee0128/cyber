"""Evidence 後端 —— 遙測資料的取回介面與其資料形狀。

resolver 只認得 `EvidenceBackend` 這個 interface，不認得 Loki。因此換掉 Loki
（改 Elasticsearch、改 Grafana Tempo……）時，改的是這裡的一個實作類別，
resolver 與 Core Event Schema 一個字都不動 —— 這是 ADR ④ 的可插拔保證。

- `LokiBackend`：真後端的佔位。本環境沒有 Loki，呼叫即明確失敗，不假裝成功。
- `FakeBackend`：測試／示範用，回傳 canned 上下文，證明 resolver 不綁死任一後端。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class BackendUnavailable(RuntimeError):
    """後端無法服務（例如 Loki 尚未接線）。明確拋出，不靜默回空。"""


@dataclass(frozen=True)
class ContextLine:
    """上下文窗裡的一行原始遙測。

    `visibility` 掛在每一行上，讓 resolver 能依呼叫者身分逐行過濾 —— 一次下鑽
    的上下文裡，有些行可能只有紫隊／教官該看到（spec §4.2）。
    """

    timestamp: datetime
    line: str
    visibility: str = "public"
    source: str = "unknown"


@dataclass(frozen=True)
class EvidenceQuery:
    """一次證據取回的參數：來源 ＋ 時間窗 ＋ 定位用 label。

    刻意是「時間窗」而非「精確一行」：分析師要看的是事件前後發生什麼（spec §4.2）。
    這個物件由 resolver 依已落地的兩份紀錄組出，**不含呼叫端傳入的查詢語法** ——
    呼叫者無法藉此越過 visibility 邊界（ADR ②）。
    """

    source: str
    start: datetime
    end: datetime
    labels: Mapping[str, str] = field(default_factory=dict)


class EvidenceBackend(Protocol):
    """遙測後端的最小契約：給一個 `EvidenceQuery`，回上下文行。

    resolver 只依賴這個介面。任何滿足它的實作都能被插進 resolver，
    這正是「換掉 Loki 不動 resolver」的可驗證形式。
    """

    def fetch_context(self, query: EvidenceQuery) -> Sequence[ContextLine]:
        """取回 `query.source` 在 `[query.start, query.end]` 時間窗內的原始上下文行。"""
        ...


@dataclass(frozen=True)
class LokiBackend:
    """真正的 Loki 後端 —— 佔位實作。本環境沒有 Loki，呼叫即明確失敗。

    它跑在 Z-MGMT，與 Loki 同區，`MGMT → Loki:3100` 是既有合法路徑；
    ticket 禁止的是 `APP → MGMT:3100`，那條在此不新增。

    接線後，`fetch_context` 應以 `base_url` 對 `query.source` ＋ 時間窗發
    LogQL `query_range`，再把結果轉成 `ContextLine`。在那之前，寧可明確
    拋出，也不回假資料 —— 假的上下文會讓分析師做出錯誤判讀。
    """

    base_url: str = "http://loki.z-mgmt:3100"

    def fetch_context(self, query: EvidenceQuery) -> Sequence[ContextLine]:
        raise BackendUnavailable(
            "LokiBackend 尚未接上真正的 Loki（本環境無 Loki）。"
            "接線後在此以 base_url 對 source + 時間窗發 LogQL query_range 並轉成 ContextLine。"
        )


@dataclass(frozen=True)
class FakeBackend:
    """可替換後端的示範／測試實作：回傳 canned 上下文。

    存在的意義是證明 resolver 只依賴 `EvidenceBackend` interface：換 backend
    不動 resolver、不動 Core Event Schema（ticket 的決定性設計測試）。

    行為刻意貼近真後端的 time-range 語意 —— 只回落在 `[start, end]` 內的行，
    這樣「回傳的是時間窗」這件事對 Fake 與 Loki 都成立。
    """

    lines: tuple[ContextLine, ...] = ()

    def fetch_context(self, query: EvidenceQuery) -> Sequence[ContextLine]:
        return tuple(
            line for line in self.lines if query.start <= line.timestamp <= query.end
        )
