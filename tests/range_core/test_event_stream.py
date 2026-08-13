"""SSE 事件流的資料面（#36）—— 游標解析、visibility、欄位遮蔽、frame 格式。

純函數層，不需要 PG。端點層（真的 HTTP 串流）在 `test_api_events.py`。
"""

import json

from disclosure import CALLER_CLEARANCE, build_label_map
from range_core.event_stream import (
    StreamEvent,
    comment_frame,
    frames_for,
    parse_last_event_id,
    project_event,
    sse_frame,
    visible_to,
)

LABELS = {"rule": build_label_map(["SQLInjectionBurst", "SSHBruteForce"], "Detection")}


def _event(visibility="public", technique="T1190", rule="SQLInjectionBurst"):
    return {
        "event_id": "evt-1",
        "event_type": "attack.detected",
        "visibility": visibility,
        "technique": technique,
        "rule": rule,
        "severity": "high",
    }


class TestLastEventIdParsing:
    def test_reads_a_plain_integer(self):
        assert parse_last_event_id("42") == 42

    def test_absent_header_starts_from_zero(self):
        assert parse_last_event_id(None) == 0

    def test_garbage_is_treated_as_absent_not_an_error(self):
        """那個值最終是使用者可控的字串，壞值不該炸掉重連。"""
        for raw in ("", "abc", "12; DROP TABLE core_events", "-5", "1.5"):
            assert parse_last_event_id(raw) == 0


class TestEventLevelVisibility:
    def test_red_does_not_receive_blue_level_events(self):
        assert visible_to(_event(visibility="blue"), CALLER_CLEARANCE["red"]) is False

    def test_blue_receives_blue_level_events(self):
        assert visible_to(_event(visibility="blue"), CALLER_CLEARANCE["blue"]) is True

    def test_everyone_receives_public_events(self):
        assert all(
            visible_to(_event(), CALLER_CLEARANCE[i]) for i in CALLER_CLEARANCE
        )

    def test_unknown_visibility_fails_closed(self):
        """看不懂的 visibility 當最嚴格一級，不是「看不懂就公開」。"""
        assert visible_to({"visibility": "brand-new"}, CALLER_CLEARANCE["blue"]) is False


class TestFieldMaskingReusesTheSharedContract:
    """#36 追加驗收條件：SSE 的欄位級遮蔽從共用契約 import，不自己做一份。"""

    def test_red_and_blue_do_not_receive_technique(self):
        for identity in ("red", "blue"):
            assert "technique" not in project_event(_event(), CALLER_CLEARANCE[identity], LABELS)

    def test_purple_and_instructor_do_receive_technique(self):
        for identity in ("purple", "instructor"):
            projected = project_event(_event(), CALLER_CLEARANCE[identity], LABELS)
            assert projected["technique"] == "T1190"

    def test_rule_name_does_not_leak_the_answer_either(self):
        projected = project_event(_event(), CALLER_CLEARANCE["blue"], LABELS)
        assert projected["rule"] == "Detection #1"

    def test_same_event_different_clearance_different_field_sets(self):
        blue = project_event(_event(), CALLER_CLEARANCE["blue"], LABELS)
        purple = project_event(_event(), CALLER_CLEARANCE["purple"], LABELS)
        assert set(purple) - set(blue) == {"technique"}

    def test_projection_does_not_mutate_the_stored_event(self):
        """落地的 Core Event 內容不變 —— 否則 P2 的 coverage 算不出來。"""
        event = _event()
        project_event(event, CALLER_CLEARANCE["red"], LABELS)
        assert event["technique"] == "T1190"


class TestFrames:
    def test_frame_carries_the_seq_as_the_sse_id(self):
        frame = sse_frame(7, {"event_id": "evt-1"})
        assert frame.startswith("id: 7\ndata: ")
        assert frame.endswith("\n\n")

    def test_frame_data_is_one_json_line(self):
        frame = sse_frame(7, {"event_id": "evt-1", "note": "多行\n會拆掉 frame"})
        data = [line for line in frame.splitlines() if line.startswith("data: ")]
        assert len(data) == 1
        assert json.loads(data[0][len("data: "):])["event_id"] == "evt-1"

    def test_keepalive_is_a_comment_not_an_event(self):
        """註解行不會被計為事件，也不會動到客戶端的 Last-Event-ID。"""
        frame = comment_frame("keep-alive")
        assert frame.startswith(":")
        assert "id:" not in frame and "data:" not in frame


class TestFramesFor:
    def test_invisible_events_produce_no_frame(self):
        batch = (
            StreamEvent(1, _event(visibility="public")),
            StreamEvent(2, _event(visibility="purple")),
        )
        seqs = [seq for seq, _ in frames_for(batch, CALLER_CLEARANCE["red"], LABELS)]
        assert seqs == [1]

    def test_frames_are_emitted_in_seq_order(self):
        batch = tuple(StreamEvent(i, _event()) for i in (3, 4, 5))
        assert [seq for seq, _ in frames_for(batch, CALLER_CLEARANCE["blue"], LABELS)] == [3, 4, 5]

    def test_masking_is_applied_to_the_streamed_payload_not_just_the_object(self):
        batch = (StreamEvent(1, _event()),)
        frames = [frame for _, frame in frames_for(batch, CALLER_CLEARANCE["blue"], LABELS)]
        assert "T1190" not in frames[0]
        assert "SQLInjectionBurst" not in frames[0]
