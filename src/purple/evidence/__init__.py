"""Evidence 解析 —— 給 event_id 取回上下文窗，依呼叫者身分過濾（spec §4）。

resolver 只依賴 `EvidenceBackend` interface，不綁死 Loki：換後端不動 resolver、
不動 Core Event Schema（ADR ④）。HTTP endpoint 由 P2 Evaluation Engine 提供，
不在本套件。
"""

from purple.evidence.backends import (
    BackendUnavailable,
    ContextLine,
    EvidenceBackend,
    EvidenceQuery,
    FakeBackend,
    LokiBackend,
)
from purple.evidence.resolver import (
    Caller,
    EvidenceBundle,
    EvidenceError,
    EvidenceNotFound,
    EvidenceResolver,
    UnknownCaller,
    build_query,
    clearance,
    filter_by_visibility,
)

__all__ = [
    "BackendUnavailable",
    "ContextLine",
    "EvidenceBackend",
    "EvidenceQuery",
    "FakeBackend",
    "LokiBackend",
    "Caller",
    "EvidenceBundle",
    "EvidenceError",
    "EvidenceNotFound",
    "EvidenceResolver",
    "UnknownCaller",
    "build_query",
    "clearance",
    "filter_by_visibility",
]
