"""Read-time score derivation (#33 WS5-5) — no stored score column, ever.

`derive_scores` is pure: it takes only completion + hint records and the
scenario that defines point values, and returns a number. It has no I/O,
so it cannot reach P2/Purple metrics (`purple.metrics.*`) even by accident
— the boundary is enforced by this function's signature first, then
mechanically by `tests/range_core/test_boundary.py`.

Score is `f(completions, hint_usages)`, recomputed on every read
(`GET /api/score` calls this fresh each time). Storing a score column
invites drift — a written value and a from-scratch recomputation can
silently disagree, and nothing would notice.

v1 answer only red-side (ADR 0002 決策②): the codebase has no Blue score
concept yet — no `blue_actions` table, no `Objective.team` field — and a
later WS8 ruling (#65) gives Blue no individual score. Shipping `{"red": ...}`
with no `blue` key makes the non-zero-sum property hold vacuously; `blue`
is an additive field for whenever WS3 delivers a Blue Action contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from range_core.objectives import HintUsage, ObjectiveCompletion
from range_core.scenarios import Scenario


@dataclass(frozen=True)
class ObjectiveScore:
    objective_id: str
    points: int
    awarded: int
    evaluation: str
    evidence_event_id: str | None
    hints_used: tuple[int, ...]
    penalty_percent: int
    completed_at: str


@dataclass(frozen=True)
class PlayerScore:
    player_id: str
    total: int
    objectives: tuple[ObjectiveScore, ...]


@dataclass(frozen=True)
class Scoreboard:
    red_total: int
    players: tuple[PlayerScore, ...]

    def as_dict(self) -> dict:
        return {
            "red": {
                "total": self.red_total,
                "players": [
                    {
                        "player_id": player.player_id,
                        "total": player.total,
                        "objectives": [
                            {
                                "objective_id": obj.objective_id,
                                "points": obj.points,
                                "awarded": obj.awarded,
                                "evaluation": obj.evaluation,
                                "evidence_event_id": obj.evidence_event_id,
                                "hints_used": list(obj.hints_used),
                                "penalty_percent": obj.penalty_percent,
                                "completed_at": obj.completed_at,
                            }
                            for obj in player.objectives
                        ],
                    }
                    for player in self.players
                ],
            }
        }


def derive_scores(
    scenario: Scenario,
    completions: tuple[ObjectiveCompletion, ...],
    hint_usages: tuple[HintUsage, ...],
) -> Scoreboard:
    """Pure function: completion + hint records -> a fully-derived Scoreboard.

    Penalty per objective is the **max** `penalty_percent` among hints the
    player used on that objective (ADR 0002 決策④), not a sum — summing can
    exceed 100% and forces a clamp that hides the modeling gap; max is the
    strongest-hint-applies rule and is easy to explain to a player. Award is
    integer floor (`points * (100 - penalty) // 100`) — never a float, so
    the number a player sees is exactly reproducible from the two record
    tables with no rounding ambiguity.
    """
    points_by_objective = {objective.id: objective.points for objective in scenario.objectives}

    hints_by_player_objective: dict[tuple[str, str], list[int]] = {}
    for usage in hint_usages:
        key = (usage.player_id, usage.objective_id)
        hints_by_player_objective.setdefault(key, []).append(usage.hint_index)

    penalty_by_player_objective: dict[tuple[str, str], int] = {}
    for usage in hint_usages:
        hints = scenario.hints_for(usage.objective_id)
        if usage.hint_index >= len(hints):
            # Skipped for penalty only, not for `hints_used` above: a usage
            # record stays truthful even if the scenario file later drops
            # the hint it pointed at. This intentionally makes `hints_used`
            # and the penalty inconsistent in that edge case (recorded fact
            # vs. derivable-today penalty) rather than losing the record.
            continue
        key = (usage.player_id, usage.objective_id)
        candidate = hints[usage.hint_index].penalty_percent
        penalty_by_player_objective[key] = max(
            penalty_by_player_objective.get(key, 0), candidate
        )

    completions_by_player: dict[str, list[ObjectiveCompletion]] = {}
    for completion in completions:
        completions_by_player.setdefault(completion.player_id, []).append(completion)

    players: list[PlayerScore] = []
    for player_id in sorted(completions_by_player):
        objective_scores: list[ObjectiveScore] = []
        for completion in completions_by_player[player_id]:
            points = points_by_objective.get(completion.objective_id)
            if points is None:
                # Deliberately silent: an edited scenario file can drop an
                # objective a player already completed under the old
                # version. There's no correct point value to award for an
                # objective that no longer exists, so it's dropped from the
                # total rather than erroring the whole scoreboard read.
                continue
            key = (player_id, completion.objective_id)
            penalty = penalty_by_player_objective.get(key, 0)
            awarded = (points * (100 - penalty)) // 100
            objective_scores.append(
                ObjectiveScore(
                    objective_id=completion.objective_id,
                    points=points,
                    awarded=awarded,
                    evaluation=completion.evaluation,
                    evidence_event_id=completion.evidence_event_id,
                    hints_used=tuple(
                        sorted(hints_by_player_objective.get(key, []))
                    ),
                    penalty_percent=penalty,
                    completed_at=completion.completed_at.isoformat(),
                )
            )
        objective_scores.sort(key=lambda o: o.objective_id)
        total = sum(o.awarded for o in objective_scores)
        players.append(
            PlayerScore(player_id=player_id, total=total, objectives=tuple(objective_scores))
        )

    return Scoreboard(red_total=sum(p.total for p in players), players=tuple(players))
