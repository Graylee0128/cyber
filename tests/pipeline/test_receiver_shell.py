"""Receiver shell —— 對真 PG 驗證編排順序與外送。"""

from purple.receiver import ingest_alert
from purple.receiver.adapters import RecordingAdapter
from purple.response.queue import InMemoryCommandQueue
from purple.store.alerts import AlertRecordStore
from purple.store.events import CoreEventStore
from purple.store.fingerprints import FingerprintIndex

import pytest

FIRING = {
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "fp-1",
            "startsAt": "2026-08-08T14:30:00+08:00",
            "labels": {
                "alertname": "SQLInjectionBurst",
                "event_type": "attack.detected",
                "technique": "T1190",
                "team": "red",
                "severity": "high",
                "scenario_id": "sqli-01",
                "exercise_id": "ex-001",
                "service": "vulnerable-app",
                "source_ip": "10.167.30.11",
            },
            "annotations": {"query": '{app="x"} |= "OR 1=1"', "threshold": "> 4 / 1m"},
            "values": {"A": 12},
        }
    ],
}

PENDING = {
    "status": "pending",
    "alerts": [{**FIRING["alerts"][0], "status": "pending"}],
}


@pytest.fixture
def wired(pg_connection):
    return (
        CoreEventStore(pg_connection),
        AlertRecordStore(pg_connection),
        RecordingAdapter(),
        InMemoryCommandQueue(),
    )


class TestIngest:
    def test_one_alert_produces_one_core_event(self, wired):
        events, records, adapter, queue = wired
        ids = ingest_alert(
            FIRING,
            events=events,
            records=records,
            adapter=adapter,
            response_queue=queue,
            exercise_id="ex-current",
            scenario_id="sqli-01",
        )
        assert len(ids) == 1
        assert events.count() == 1

    def test_alert_record_written_before_core_event(self, wired):
        """順序不可顛倒：Core Event 存在時，對應的 Alert Record 必已存在。"""
        events, records, adapter, queue = wired
        [event_id] = ingest_alert(
            FIRING, events=events, records=records, adapter=adapter,
            response_queue=queue,
            exercise_id="ex-current",
            scenario_id="sqli-01",
        )
        assert records.exists(event_id)
        assert records.by_id(event_id)["backend"] == "loki"

    def test_event_id_is_shared_by_both_records(self, wired):
        events, records, adapter, queue = wired
        [event_id] = ingest_alert(
            FIRING, events=events, records=records, adapter=adapter,
            response_queue=queue,
            exercise_id="ex-current",
            scenario_id="sqli-01",
        )
        assert events.by_id(event_id)[0]["event_id"] == event_id
        assert records.by_id(event_id)["event_id"] == event_id

    def test_downstream_adapter_receives_the_core_event(self, wired):
        events, records, adapter, queue = wired
        ingest_alert(
            FIRING, events=events, records=records, adapter=adapter,
            response_queue=queue,
            exercise_id="ex-current",
            scenario_id="sqli-01",
        )
        assert len(adapter.delivered) == 1
        assert adapter.delivered[0]["scenario_id"] == "sqli-01"

    def test_attack_enqueues_response_command_not_direct_write(self, wired):
        """票 09 的 contract：attack.detected 把封鎖需求放進佇列，不直寫 ipset。
        直寫需要連入 target，會破壞 TARGET→MGMT 單向。這裡驗的是測試載具路徑
        （auto_response=True）——鏈路本身不因票 48 改變，見 #17。"""
        events, records, adapter, queue = wired
        ingest_alert(
            FIRING,
            events=events,
            records=records,
            adapter=adapter,
            response_queue=queue,
            exercise_id="ex-current",
            scenario_id="sqli-01",
            auto_response=True,
        )
        commands = queue.claim()
        assert len(commands) == 1
        assert commands[0].action == "block"
        assert commands[0].source_ip == "10.167.30.11"

    def test_auto_response_commands_are_tagged_and_excluded_from_scoring(self, wired):
        """票 48：自動模式產生的封鎖不計入任何藍隊分數。計分（#33）以
        `triggered_by` 為唯一判準，這裡斷言自動路徑一定標成 "auto"，
        絕不能悄悄長成 "manual"。"""
        events, records, adapter, queue = wired
        ingest_alert(
            FIRING,
            events=events,
            records=records,
            adapter=adapter,
            response_queue=queue,
            exercise_id="ex-current",
            scenario_id="sqli-01",
            auto_response=True,
        )
        [command] = queue.claim()
        assert command.triggered_by == "auto"

    def test_exercise_mode_does_not_auto_enqueue(self, wired):
        """票 48：演練模式（預設）下，attack.detected 不會自動 enqueue —— 待處置
        建議就是這筆事件本身，真正的 enqueue 由藍隊動作觸發（#51）。把降級邏輯
        拿掉（讓 auto_response 預設值失去作用）時，這條測試必須變紅。"""
        events, records, adapter, queue = wired
        ingest_alert(
            FIRING,
            events=events,
            records=records,
            adapter=adapter,
            response_queue=queue,
            exercise_id="ex-current",
            scenario_id="sqli-01",
        )
        assert queue.claim() == []
        assert events.count() == 1  # 事件本身還是照常入庫，只是沒有自動封鎖

    def test_repeat_notification_of_the_same_firing_alert_does_not_reenqueue(
        self, wired, pg_connection
    ):
        """policies.yaml 的 repeat_interval 讓 Grafana 對同一個持續 firing 的 alert
        反覆重送 webhook（同一個 fingerprint／同一個 event_id）。重送不該讓同一次
        攻擊被重複封鎖、重複產生 response.executed（#17 real-range 上實測到
        15s 一次的重送會不斷 enqueue，直到我們把 enqueue 綁到「這次是不是真的
        新事件」為止）。

        兩次呼叫必須共用同一個 fingerprint index，重送的 alert 才會鑄成同一個
        event_id——生產環境的 server.py 就是這樣接的；沒有 fingerprints 時
        ingest_alert 每次都會鑄新 id，反而測不出重送的情境。
        """
        events, records, adapter, queue = wired
        fingerprints = FingerprintIndex(pg_connection, "ex-current")
        ingest_alert(
            FIRING,
            events=events,
            records=records,
            adapter=adapter,
            response_queue=queue,
            fingerprints=fingerprints,
            exercise_id="ex-current",
            scenario_id="sqli-01",
            auto_response=True,
        )
        ingest_alert(
            FIRING,
            events=events,
            records=records,
            adapter=adapter,
            response_queue=queue,
            fingerprints=fingerprints,
            exercise_id="ex-current",
            scenario_id="sqli-01",
            auto_response=True,
        )
        commands = queue.claim()
        assert len(commands) == 1

    def test_pending_produces_nothing(self, wired):
        events, records, adapter, queue = wired
        ids = ingest_alert(
            PENDING, events=events, records=records, adapter=adapter,
            response_queue=queue,
            exercise_id="ex-current",
            scenario_id="sqli-01",
        )
        assert ids == []
        assert events.count() == 0
