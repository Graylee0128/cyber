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


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE action_registries SET scenario_id='other' WHERE exercise_id='ex-21'",
        "DELETE FROM action_registries WHERE exercise_id='ex-21'",
    ],
)
def test_database_rejects_frozen_registry_header_change_or_delete(seeded, statement):
    seeded.freeze("ex-21")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="is frozen"):
        seeded.conn.execute(statement)


def test_database_rejects_direct_unfreeze(seeded):
    seeded.freeze("ex-21")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="is frozen"):
        seeded.conn.execute(
            "UPDATE action_registries SET frozen_at=NULL WHERE exercise_id='ex-21'"
        )


def test_database_rejects_moving_action_out_of_frozen_registry(seeded):
    seeded.create("ex-other", "sqli-01")
    seeded.freeze("ex-21")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="ex-21"):
        seeded.conn.execute(
            "UPDATE registered_actions SET exercise_id='ex-other' "
            "WHERE exercise_id='ex-21' AND action_id='a-1'"
        )


def test_database_rejects_moving_action_into_frozen_registry(seeded):
    seeded.create("ex-other", "sqli-01")
    seeded.add("ex-other", RegisteredAction("a-3", "T1059", "other"))
    seeded.freeze("ex-21")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="ex-21"):
        seeded.conn.execute(
            "UPDATE registered_actions SET exercise_id='ex-21' "
            "WHERE exercise_id='ex-other' AND action_id='a-3'"
        )


def test_freeze_serializes_with_concurrent_action_write(pg_connection):
    """A writer that started before freeze must not commit after freeze."""
    import threading
    from purple.store.db import connect

    registry = ActionRegistryStore(pg_connection, load_whitelist())
    registry.seed(
        "ex-race",
        "sqli-01",
        [RegisteredAction("a-1", "T1190", "first")],
    )
    writer_started = threading.Event()
    release_writer = threading.Event()
    writer_done = threading.Event()
    freezer_done = threading.Event()
    outcome = []

    def writer():
        conn = connect()
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO registered_actions VALUES "
                    "('ex-race', 'a-2', 'T1059', 'racing write')"
                )
                # Signal only after the INSERT trigger has acquired its registry lock.
                writer_started.set()
                assert release_writer.wait(2)
            outcome.append("committed")
        finally:
            conn.close()
            writer_done.set()

    def freezer():
        try:
            registry.freeze("ex-race")
        finally:
            freezer_done.set()

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    assert writer_started.wait(1)
    freezer_thread = threading.Thread(target=freezer)
    freezer_thread.start()
    # The trigger's registry-row lock is the only reason freeze must wait here.
    assert not freezer_done.wait(0.1)
    release_writer.set()
    assert writer_done.wait(2)
    assert freezer_done.wait(2)
    writer_thread.join()
    freezer_thread.join()
    # The write serialized before freeze and is therefore part of the frozen snapshot;
    # it must never appear after an already completed freeze.
    assert outcome == ["committed"]
    assert registry.denominator("ex-race") == ("a-1", "a-2")


def test_frozen_at_is_stable_when_freeze_is_retried(seeded):
    first = seeded.freeze("ex-21").frozen_at
    second = seeded.freeze("ex-21").frozen_at
    assert second == first
