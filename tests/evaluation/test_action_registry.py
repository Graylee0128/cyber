import psycopg
import pytest

from purple.evaluation.action_registry import (
    ActionRegistryStore,
    RegisteredAction,
    RegistryFrozen,
    RegistryNotFrozen,
)
from purple.receiver.whitelist import TechniqueRejected, load_whitelist


@pytest.fixture
def registry(pg_connection):
    return ActionRegistryStore(pg_connection, load_whitelist())


@pytest.fixture
def seeded(registry):
    registry.seed(
        "ex-21",
        "sqli-01",
        [
            RegisteredAction("a-1", "T1190", "exploit public app"),
            RegisteredAction("a-2", "T1059", "run command"),
        ],
    )
    return registry


def test_seed_is_queryable_and_ordered(seeded):
    snapshot = seeded.get("ex-21")
    assert snapshot.scenario_id == "sqli-01"
    assert [action.id for action in snapshot.actions] == ["a-1", "a-2"]
    assert snapshot.frozen_at is None


def test_unknown_technique_is_rejected(registry):
    registry.create("ex-21", "sqli-01")
    with pytest.raises(TechniqueRejected, match="T9999"):
        registry.add("ex-21", RegisteredAction("a-1", "T9999", "unknown"))


def test_denominator_only_comes_from_frozen_registry(seeded):
    with pytest.raises(RegistryNotFrozen):
        seeded.denominator("ex-21")
    snapshot = seeded.freeze("ex-21")
    assert snapshot.frozen_at is not None
    assert seeded.denominator("ex-21") == ("a-1", "a-2")


@pytest.mark.parametrize("operation", ["add", "update", "delete"])
def test_application_rejects_every_mutation_after_freeze(seeded, operation):
    seeded.freeze("ex-21")
    action = RegisteredAction("a-1", "T1190", "changed")
    with pytest.raises(RegistryFrozen, match="frozen since"):
        if operation == "add":
            seeded.add("ex-21", RegisteredAction("a-3", "T1005", "new"))
        elif operation == "update":
            seeded.update("ex-21", action)
        else:
            seeded.delete("ex-21", "a-1")


def test_database_trigger_rejects_mutation_even_if_application_guard_is_removed(seeded):
    seeded.freeze("ex-21")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="is frozen"):
        seeded.conn.execute(
            "UPDATE registered_actions SET description='bypassed' "
            "WHERE exercise_id='ex-21' AND action_id='a-1'"
        )


def test_frozen_at_is_stable_when_freeze_is_retried(seeded):
    first = seeded.freeze("ex-21").frozen_at
    second = seeded.freeze("ex-21").frozen_at
    assert second == first
