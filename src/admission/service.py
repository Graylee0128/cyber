from __future__ import annotations

import ipaddress
import logging
from typing import Any, Protocol

from admission.store.pool import PoolConfigStore
from admission.store.seats import SeatStore

log = logging.getLogger("admission.service")


class RangePublisher(Protocol):
    def publish_player(self, **player: Any) -> None: ...
    def revoke_player(self, exercise_id: str, player_id: str) -> None: ...
    def prepare(self, scenario_id: str) -> dict[str, Any]: ...
    def current_preparation(self) -> dict[str, Any] | None: ...
    def cancel_preparation(self, exercise_id: str) -> bool: ...


class Alerter(Protocol):
    def notify(self, **alert: Any) -> None: ...


class NullRangePublisher:
    def publish_player(self, **player: Any) -> None: del player
    def revoke_player(self, exercise_id: str, player_id: str) -> None: del exercise_id, player_id
    def prepare(self, scenario_id: str) -> dict[str, Any]:
        # 跟 publish/revoke 的靜默 no-op 不一樣——prepare 沒有「假裝成功」的安全
        # 選項，呼叫端需要 Range Core 真的生成的 exercise_id 才能繼續，吞掉等於
        # 把「沒接 Range Core」偽裝成「已經準備好了」，寧可噴出去讓教官看到。
        raise RuntimeError("range core publication not configured")
    def current_preparation(self) -> dict[str, Any] | None:
        # 這裡可以老實回 None（#163）：「沒接 Range Core」跟「沒有 prepared」
        # 對呼叫端是同一種可接受結果——UI 忘記 exercise_id 時最多是查不到，
        # 不像 `prepare` 那樣會讓呼叫端誤以為場次已經準備好了。
        return None
    def cancel_preparation(self, exercise_id: str) -> bool:
        del exercise_id
        return False


class NullAlerter:
    def notify(self, **alert: Any) -> None: del alert


class TeamFull(Exception):
    def __init__(self, team: str): self.team = team


class AdmissionService:
    def __init__(self, conn, *, publisher: RangePublisher | None = None,
                 alerter: Alerter | None = None, session_ttl_seconds: int | None = None):
        self.conn = conn
        self.seats = SeatStore(conn)
        self.pools = PoolConfigStore(conn)
        self.publisher = publisher or NullRangePublisher()
        self.alerter = alerter or NullAlerter()
        self.session_ttl_seconds = session_ttl_seconds

    def allocate(self, exercise_id: str, team: str) -> dict[str, Any]:
        cfg = self.pools.get(exercise_id)
        if cfg is None:
            raise TeamFull(team)
        result = self.seats.request_seat(exercise_id, team, cfg.red_cap, False)
        if result is None:
            raise TeamFull(team)
        return {**result, "exercise_id": exercise_id, "team": team, "state": "requested"}

    def claim_with_remote_token(self, exercise_id: str, team: str, token: str) -> dict[str, Any] | None:
        try:
            with self.conn.transaction():
                if not self.seats.consume_remote_link(exercise_id, token):
                    return None
                return self.allocate(exercise_id, team)
        except TeamFull:
            raise

    def prepare(self, scenario_id: str) -> dict[str, Any]:
        """代教官呼叫 Range Core 的 prepare（#143 項目 1）。純轉發——Admission
        自己的 seat/pool 狀態不需要跟著這一步改變，exercise_id 由 Range Core
        決定，這裡不寫回任何 Admission 表。"""
        return self.publisher.prepare(scenario_id)

    def current_preparation(self) -> dict[str, Any] | None:
        """代教官查詢目前是否有一筆 `prepared`（#163）。純轉發，同 `prepare`。"""
        return self.publisher.current_preparation()

    def cancel_preparation(self, exercise_id: str) -> bool:
        """代教官取消一筆 `prepared`（#163）。純轉發，同 `prepare`。"""
        return self.publisher.cancel_preparation(exercise_id)

    def bind_session(self, seat_id: str) -> str:
        if self.session_ttl_seconds is None:
            raise RuntimeError("ADMISSION_SESSION_TTL_SECONDS is required")
        return self.seats.bind_session(seat_id, self.session_ttl_seconds)

    def logout(self, token: str | None) -> bool:
        return self.seats.revoke_session(token) if token else False

    def resolve_session(self, token: str | None) -> dict[str, Any] | None:
        return self.seats.resolve_session(token) if token else None

    def ready(self, seat_id: str, endpoints: list[dict[str, Any]]) -> bool:
        with self.conn.transaction():
            seat = self.seats.get_for_update(seat_id)
            if seat is None:
                return False
            self.validate_endpoints(seat["team"], endpoints)
            if seat["state"] == "requested":
                self.seats.mark_ready(seat_id, endpoints)
                seat = self.seats.get(seat_id)
            if seat is None or seat["state"] not in ("ready", "claimed"):
                return False
            if seat["published_at"] is None:
                self.publisher.publish_player(
                    player_id=seat["player_id"], exercise_id=seat["exercise_id"], team=seat["team"],
                    source_ip=endpoints[0]["host"] if seat["team"] == "red" else None,
                )
                self.seats.mark_published(seat_id)
        return True

    @staticmethod
    def validate_endpoints(team: str, endpoints: list[dict[str, Any]]) -> None:
        expected = {"red": {"main"}, "blue": {"a", "b"}}[team]
        if {x.get("terminal") for x in endpoints} != expected or len(endpoints) != len(expected):
            raise ValueError("terminal set does not match seat team")
        network = ipaddress.ip_network("10.167.30.0/24" if team == "red" else "10.167.60.0/24")
        for endpoint in endpoints:
            try:
                host = ipaddress.ip_address(endpoint.get("host", ""))
            except ValueError as exc:
                raise ValueError("endpoint host must be a literal approved-zone IP") from exc
            if host not in network or endpoint.get("port") != 7681:
                raise ValueError("endpoint is outside the approved ttyd boundary")

    def rebind(self, seat_id: str, *, actor: str) -> str:
        seat = self.seats.get(seat_id)
        if seat is None or seat["state"] not in ("requested", "ready", "claimed"):
            raise KeyError(seat_id)
        with self.conn.transaction():
            self.seats.revoke_sessions(seat_id)
            token = self.bind_session(seat_id)
            self.conn.execute(
                "INSERT INTO admission_audit(actor,seat_id,action) VALUES (%s,%s,'rebind')",
                (actor, seat_id),
            )
        return token

    def release(self, seat_id: str, *, actor: str) -> bool:
        with self.conn.transaction():
            seat = self.seats.get_for_update(seat_id)
            if seat is None:
                return False
            if seat["player_id"]:
                self.publisher.revoke_player(seat["exercise_id"], seat["player_id"])
            self.seats.revoke_sessions(seat_id)
            self.seats.release(seat_id)
            self.conn.execute(
                "INSERT INTO admission_audit(actor,seat_id,action) VALUES (%s,%s,'release')",
                (actor, seat_id),
            )
        return True

    def authorize(self, token: str | None, terminal: str) -> str | None:
        seat = self.resolve_session(token)
        if seat is None or seat["state"] not in ("ready", "claimed"):
            return None
        endpoint = next((x for x in seat["endpoints"] if x.get("terminal") == terminal), None)
        if endpoint is None:
            return None
        try:
            self.validate_endpoints(seat["team"], seat["endpoints"])
        except ValueError:
            return None
        self.seats.claim_for_access(seat["seat_id"])
        return f"{endpoint['host']}:{endpoint['port']}"

    def seat_source_ip(self, token: str | None) -> str | None:
        """這個 session 綁的座位對 Range Core 而言的來源 IP（#126 item 4）。

        名冊的鍵是紅隊那台 kali 自己的位址，所以只有紅隊有值——藍隊不進個人
        計分（WS8 spec §6.5.1），沒有 flag 提交也沒有 hint，本來就不需要名冊
        歸屬。回傳 None 時 Z-EDGE 不設標頭，Range Core 就退回看 TCP peer。

        取 `endpoints[0]["host"]` 與 `AdmissionService.ready()` 交給 Range Core
        的 `source_ip` 是同一個值、同一個取法——名冊在領位時就是照這個欄位登記
        的，這裡若換一種取法，兩邊會在多端點座位上悄悄對不起來。
        """
        seat = self.resolve_session(token)
        if seat is None or seat["state"] not in ("ready", "claimed"):
            return None
        if seat["team"] != "red":
            return None
        endpoints = seat["endpoints"] or []
        return endpoints[0]["host"] if endpoints else None

    def expire_requested(self, timeout_seconds: int) -> dict[str, int]:
        """Retry or release stale requested seats atomically.

        The first expiry starts the single automatic retry by refreshing
        ``requested_at`` with ``retry_count=1``. The second persists release
        and its operator alert before attempting any optional notification.
        """
        result = {"retry": 0, "released": 0}
        notifications: list[dict[str, str]] = []
        with self.conn.transaction():
            rows = self.conn.execute(
                """SELECT seat_id,exercise_id,player_id,retry_count FROM seat
                   WHERE state='requested'
                     AND requested_at < now() - (%s * interval '1 second')
                   FOR UPDATE SKIP LOCKED""",
                (timeout_seconds,),
            ).fetchall()
            for seat_id, exercise_id, player_id, retry_count in rows:
                if retry_count == 0:
                    self.conn.execute(
                        """UPDATE seat SET state='requested',retry_count=1,requested_at=now()
                           WHERE seat_id=%s AND state='requested'""",
                        (seat_id,),
                    )
                    result["retry"] += 1
                else:
                    self.seats.revoke_sessions(seat_id)
                    self.seats.release(seat_id)
                    self.publisher.revoke_player(exercise_id, player_id)
                    self.conn.execute(
                        """INSERT INTO admission_alert(seat_id,reason)
                           VALUES (%s,'provisioning_timeout')
                           ON CONFLICT (seat_id,reason) DO NOTHING""",
                        (seat_id,),
                    )
                    notifications.append(
                        {"seat_id": seat_id, "reason": "provisioning_timeout"}
                    )
                    result["released"] += 1
        for notification in notifications:
            try:
                self.alerter.notify(**notification)
            except Exception:
                log.exception("optional admission timeout notifier failed")
        return result

    def expire_requests(self, timeout_seconds: int) -> dict[str, int]:
        """Compatibility alias for callers predating the sweeper entrypoint."""
        return self.expire_requested(timeout_seconds)
