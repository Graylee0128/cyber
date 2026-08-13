"""HTTP API for Action Registry phase of the P2 Evaluation Engine."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from purple.evaluation.action_registry import (
    ActionRegistryStore,
    RegisteredAction,
    RegistryError,
)
from purple.receiver.whitelist import TechniqueRejected, default_whitelist
from purple.store.db import connect, ensure_schema


class ActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    technique: str = Field(min_length=1)
    description: str = Field(min_length=1)


class RegistryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1)
    actions: tuple[ActionInput, ...] = Field(min_length=1)


def create_app(connection_factory=connect) -> FastAPI:
    app = FastAPI(title="Purple Evaluation Engine")

    def run(operation):
        conn = connection_factory()
        try:
            ensure_schema(conn)
            return operation(ActionRegistryStore(conn, default_whitelist()))
        except (RegistryError, TechniqueRejected) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            conn.close()

    @app.post("/api/exercises/{exercise_id}/actions", status_code=201)
    def register(exercise_id: str, payload: RegistryInput):
        return run(
            lambda store: asdict(
                store.seed(
                    exercise_id,
                    payload.scenario_id,
                    (RegisteredAction(**action.model_dump()) for action in payload.actions),
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
