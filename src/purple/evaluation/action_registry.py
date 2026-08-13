"""Frozen Action Registry: the only source of the P2 denominator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import psycopg

from purple.receiver.whitelist import Whitelist, check_technique


class RegistryError(ValueError):
    """The requested registry operation violates its lifecycle contract."""


class RegistryFrozen(RegistryError):
    pass


class RegistryNotFrozen(RegistryError):
    pass


@dataclass(frozen=True)
class RegisteredAction:
    id: str
    technique: str
    description: str


@dataclass(frozen=True)
class RegistrySnapshot:
    exercise_id: str
    scenario_id: str
    frozen_at: datetime | None
    actions: tuple[RegisteredAction, ...]


@dataclass
class ActionRegistryStore:
    conn: psycopg.Connection
    whitelist: Whitelist

    def create(self, exercise_id: str, scenario_id: str) -> RegistrySnapshot:
        if not exercise_id or not scenario_id:
            raise RegistryError("exercise_id and scenario_id are required")
        self.conn.execute(
            "INSERT INTO action_registries (exercise_id, scenario_id) VALUES (%s, %s)",
            (exercise_id, scenario_id),
        )
        return self.get(exercise_id)

    def seed(
        self,
        exercise_id: str,
        scenario_id: str,
        actions: Iterable[RegisteredAction],
    ) -> RegistrySnapshot:
        actions = tuple(actions)
        if not actions:
            raise RegistryError("at least one action is required")
        with self.conn.transaction():
            self.create(exercise_id, scenario_id)
            for action in actions:
                self.add(exercise_id, action)
        return self.get(exercise_id)

    def add(self, exercise_id: str, action: RegisteredAction) -> None:
        self._validate_action(action)
        self._require_mutable(exercise_id)
        self.conn.execute(
            "INSERT INTO registered_actions "
            "(exercise_id, action_id, technique, description) VALUES (%s, %s, %s, %s)",
            (exercise_id, action.id, action.technique, action.description),
        )

    def update(self, exercise_id: str, action: RegisteredAction) -> None:
        self._validate_action(action)
        self._require_mutable(exercise_id)
        cursor = self.conn.execute(
            "UPDATE registered_actions SET technique=%s, description=%s "
            "WHERE exercise_id=%s AND action_id=%s",
            (action.technique, action.description, exercise_id, action.id),
        )
        if cursor.rowcount != 1:
            raise RegistryError(f"unknown action {action.id!r}")

    def delete(self, exercise_id: str, action_id: str) -> None:
        self._require_mutable(exercise_id)
        cursor = self.conn.execute(
            "DELETE FROM registered_actions WHERE exercise_id=%s AND action_id=%s",
            (exercise_id, action_id),
        )
        if cursor.rowcount != 1:
            raise RegistryError(f"unknown action {action_id!r}")

    def freeze(self, exercise_id: str) -> RegistrySnapshot:
        with self.conn.transaction():
            row = self.conn.execute(
                "SELECT frozen_at FROM action_registries WHERE exercise_id=%s FOR UPDATE",
                (exercise_id,),
            ).fetchone()
            if row is None:
                raise RegistryError(f"unknown registry {exercise_id!r}")
            if row[0] is None:
                count = self.conn.execute(
                    "SELECT count(*) FROM registered_actions WHERE exercise_id=%s",
                    (exercise_id,),
                ).fetchone()[0]
                if count == 0:
                    raise RegistryError("cannot freeze an empty action registry")
                self.conn.execute(
                    "UPDATE action_registries SET frozen_at=now() WHERE exercise_id=%s",
                    (exercise_id,),
                )
        return self.get(exercise_id)

    def get(self, exercise_id: str) -> RegistrySnapshot:
        row = self.conn.execute(
            "SELECT scenario_id, frozen_at FROM action_registries WHERE exercise_id=%s",
            (exercise_id,),
        ).fetchone()
        if row is None:
            raise RegistryError(f"unknown registry {exercise_id!r}")
        actions = self.conn.execute(
            "SELECT action_id, technique, description FROM registered_actions "
            "WHERE exercise_id=%s ORDER BY action_id",
            (exercise_id,),
        ).fetchall()
        return RegistrySnapshot(
            exercise_id=exercise_id,
            scenario_id=row[0],
            frozen_at=row[1],
            actions=tuple(RegisteredAction(*action) for action in actions),
        )

    def denominator(self, exercise_id: str) -> tuple[str, ...]:
        snapshot = self.get(exercise_id)
        if snapshot.frozen_at is None:
            raise RegistryNotFrozen("coverage denominator requires a frozen registry")
        return tuple(action.id for action in snapshot.actions)

    def _require_mutable(self, exercise_id: str) -> None:
        row = self.conn.execute(
            "SELECT frozen_at FROM action_registries WHERE exercise_id=%s",
            (exercise_id,),
        ).fetchone()
        if row is None:
            raise RegistryError(f"unknown registry {exercise_id!r}")
        if row[0] is not None:
            raise RegistryFrozen(
                f"action registry {exercise_id!r} is frozen since {row[0].isoformat()}"
            )

    def _validate_action(self, action: RegisteredAction) -> None:
        if not action.id or not action.description:
            raise RegistryError("action id and description are required")
        check_technique(action.technique, self.whitelist)
