"""HTTP API for the P2 Evaluation Engine: Action Registry and evaluation results."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
import psycopg
from pydantic import BaseModel, ConfigDict, Field

from purple.evaluation.action_registry import (
    ActionRegistryStore,
    RegisteredAction,
    RegistryError,
    RegistryNotFound,
)
from purple.evaluation.assembly import EvaluationAssembler
from purple.evaluation.evaluator import EvaluationService
from purple.evidence.backends import BackendUnavailable
from purple.receiver.whitelist import TechniqueRejected, default_whitelist
from purple.store.alerts import AlertRecordStore
from purple.store.db import connect, ensure_schema
from purple.store.events import CoreEventStore
from purple.store.executions import ActionExecutionStore
from range_core.scenarios import ScenarioCatalog


def render_evaluation(service: EvaluationService) -> dict[str, Any]:
    """把評分結果序列化成 JSON-able dict（純函數，可獨立測）。

    四個核心數字**同進同出**（#21 acceptance）：coverage、confirmation rate、
    alert volume 與 excluded counts 在同一個 `metrics` 物件裡，沒有任何一個
    endpoint 只吐其中一部分。單獨吐 coverage 會讓讀者無從得知那個數字排除了多少
    動作 —— 一個 100% 的涵蓋率，底下可能有九成動作是 `unknown`。

    分母為 0 時 `action_coverage` 與 `confirmation_rate` 是 JSON `null`，不是 0。
    `null` 說的是「沒有可算的東西」，`0` 說的是「算出來是零」，兩者對藍隊的
    意思完全相反。
    """
    return {
        "metrics": service.metrics().as_dict(),
        "actions": [
            {
                "action_id": result.action_id,
                "state": result.state.value,
                "level": result.level.value if result.level else None,
                "gap": result.gap,
                "reason": result.reason,
                "event_ids": list(result.event_ids),
            }
            for result in service.evaluate()
        ],
    }


class RegistryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1)


def create_app(
    connection_factory=connect,
    catalog: ScenarioCatalog | None = None,
    *,
    source_registry: Callable[[str], Any] | None = None,
    telemetry: Any = None,
) -> FastAPI:
    """建立 Evaluation Engine 的 app。

    `source_registry`（scenario_id -> Registry）與 `telemetry`（EvidenceBackend）
    可注入以便測試。production 預設分別是 Loki heartbeat 推導的 Source Registry
    與 `LokiBackend` —— **沒有 in-memory fallback**：接不上真後端時 evaluation
    必須 503，而不是拿空資料算出一份好看的報告。
    """
    app = FastAPI(title="Purple Evaluation Engine")
    scenario_catalog = catalog or ScenarioCatalog.from_directory("scenarios")

    def _session(operation):
        conn = connection_factory()
        try:
            ensure_schema(conn)
            return operation(conn)
        except RegistryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (RegistryError, TechniqueRejected, psycopg.IntegrityError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BackendUnavailable as exc:
            # 遙測後端故障。503 而非 200＋空資料：後者會被讀成「藍隊什麼都沒看到」。
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            conn.close()

    def run(operation):
        return _session(lambda conn: operation(ActionRegistryStore(conn, default_whitelist())))

    def _assembler(conn) -> EvaluationAssembler:
        resolve_registry = source_registry
        if resolve_registry is None:
            from purple.registry.production import registry_for_scenario

            resolve_registry = registry_for_scenario
        backend = telemetry
        if backend is None:
            import os

            from purple.evidence.backends import LokiBackend

            backend = LokiBackend(
                base_url=os.environ.get("PURPLE_LOKI_URL", "http://loki:3100")
            )
        return EvaluationAssembler(
            registry=ActionRegistryStore(conn, default_whitelist()),
            events=CoreEventStore(conn),
            records=AlertRecordStore(conn),
            executions=ActionExecutionStore(conn),
            source_registry=resolve_registry,
            telemetry=backend,
        )

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

    @app.get("/api/exercises/{exercise_id}/evaluation")
    def get_evaluation(exercise_id: str):
        """四態判定結果 ＋ 四個核心數字。registry 未凍結 → 409。

        未凍結不是「還沒有資料」而是「這個分母還會變」，用它算出來的任何比率
        都無法判斷準不準（#21 §3.2 陷阱①）。所以是 409 conflict，不是 200 空集合。
        """
        return _session(lambda conn: render_evaluation(_assembler(conn).build(exercise_id)))

    return app


app = create_app()
