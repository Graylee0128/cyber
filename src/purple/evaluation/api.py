"""HTTP API for Action Registry phase of the P2 Evaluation Engine."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
import psycopg
from pydantic import BaseModel, ConfigDict, Field

from purple.evaluation.action_registry import (
    ActionRegistryStore,
    RegisteredAction,
    RegistryError,
    RegistryNotFound,
)
from purple.receiver.whitelist import TechniqueRejected, default_whitelist
from purple.store.db import connect, ensure_schema
from range_core.scenarios import ScenarioCatalog


class RegistryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1)


def create_app(connection_factory=connect, catalog: ScenarioCatalog | None = None) -> FastAPI:
    app = FastAPI(title="Purple Evaluation Engine")
    scenario_catalog = catalog or ScenarioCatalog.from_directory("scenarios")

    def run(operation):
        conn = connection_factory()
        try:
            ensure_schema(conn)
            return operation(ActionRegistryStore(conn, default_whitelist()))
        except RegistryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (RegistryError, TechniqueRejected, psycopg.IntegrityError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            conn.close()

    @app.post("/api/exercises/{exercise_id}/actions", status_code=201)
    def register(exercise_id: str, payload: RegistryInput):
        scenario = next(
            (item for item in scenario_catalog.scenarios if item.id == payload.scenario_id),
            None,
        )
        if scenario is None:
            raise HTTPException(status_code=404, detail="unknown scenario")
        return run(
            lambda store: asdict(
                store.seed(
                    exercise_id,
                    payload.scenario_id,
                    (
                        RegisteredAction(action.id, action.technique, action.description)
                        for action in scenario.action_registry_seed()
                    ),
                )
            )
        )

    @app.post("/api/exercises/{exercise_id}/actions/freeze")
    def freeze(exercise_id: str):
        return run(lambda store: asdict(store.freeze(exercise_id)))

    @app.get("/api/exercises/{exercise_id}/actions")
    def get_registry(exercise_id: str):
        return run(lambda store: asdict(store.get(exercise_id)))

    return app


app = create_app()
