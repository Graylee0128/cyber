"""載具自身的 smoke test —— 票 02a 的核心紅燈。

「載具最常見的缺陷是永遠綠。一個從不會紅的載具，比沒有載具更糟。」

這裡把 store（捕捉）＋ waiting（等待）接起來，證明兩件事：
1. 事件真的出現時，載具抓得到。
2. 事件**沒**出現時，載具會**失敗**，而不是靜靜通過。

第 2 條才是重點。它跑真的 PG，因為載具捕捉事件靠的就是 store。
"""

from purple.harness import EventNotSeen, assert_core_event, wait_for_event
from purple.store.events import CoreEventStore

import pytest

CORE_EVENT = {
    "event_id": "evt-01J00000000000000000SMOKE",
    "exercise_id": "ex-smoke",
    "scenario_id": "sqli-01",
    "event_type": "attack.detected",
    "lifecycle": "firing",
    "severity": "high",
    "source": "grafana",
    "team": "red",
    "technique": "T1190",
    "target": {"service": "vulnerable-app"},
    "observed_at": "2026-08-08T14:30:00+08:00",
    "visibility": "public",
    "action_id": None,
}


@pytest.fixture
def store(pg_connection):
    return CoreEventStore(pg_connection)


def test_harness_captures_an_event_that_appears(store):
    mark = store.now()
    store.append(CORE_EVENT)

    found = wait_for_event(
        fetch=lambda: store.since(mark),
        match=lambda e: e["scenario_id"] == "sqli-01",
        what="sqli-01 attack.detected",
        timeout_s=2,
        poll_s=0.1,
    )
    assert_core_event(found)
    assert found["event_id"] == CORE_EVENT["event_id"]


def test_harness_fails_when_no_event_appears(store):
    """核心紅燈：什麼都不寫進 store，載具必須逾時失敗，不能通過。"""
    mark = store.now()

    with pytest.raises(EventNotSeen, match="完全沒有"):
        wait_for_event(
            fetch=lambda: store.since(mark),
            match=lambda e: True,
            what="any event",
            timeout_s=1,
            poll_s=0.1,
        )


def test_harness_fails_when_only_the_wrong_event_appears(store):
    """有事件、但不是我們等的那一筆 —— 也必須失敗，且訊息要列出看到了什麼。"""
    mark = store.now()
    store.append(CORE_EVENT)

    with pytest.raises(EventNotSeen) as exc:
        wait_for_event(
            fetch=lambda: store.since(mark),
            match=lambda e: e["scenario_id"] == "does-not-exist",
            what="does-not-exist scenario",
            timeout_s=1,
            poll_s=0.1,
        )
    assert "sqli-01" in str(exc.value)
