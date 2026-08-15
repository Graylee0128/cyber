"""HTTP API for the P2 Evaluation Engine: Action Registry and evaluation results."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
import psycopg
from pydantic import BaseModel, ConfigDict, Field

from purple.battleboard.sanitize import (
    build_attack_label_map,
    project_instructor_event,
    project_public_event,
)
from purple.console.drilldown import telemetry_mark
from purple.evaluation.action_registry import (
    ActionRegistryStore,
    RegisteredAction,
    RegistryError,
    RegistryNotFound,
)
from purple.evaluation.assembly import EvaluationAssembler
from purple.evaluation.evaluator import EvaluationService
from purple.evaluation.latency import LatencyAssembler, LatencySummary, summarize_latency
from purple.evidence.backends import BackendUnavailable
from purple.receiver.whitelist import TechniqueRejected, default_whitelist
from purple.registry.production import CatalogError
from purple.store.alerts import AlertRecordStore
from purple.store.db import connect, ensure_schema
from purple.store.events import CoreEventStore
from purple.store.executions import ActionExecutionStore
from purple.store.latency import LatencySummaryStore
from range_core.scenarios import ScenarioCatalog


def render_latency(summaries: dict[str, LatencySummary]) -> dict[str, Any]:
    """把每個 mode 的 latency 摘要序列化（純函數）。

    `exercise` 與 `automatic` 各自成一筆，永不合併 —— 人在迴圈的 MTTR 與自動模式
    是不同的量。每筆帶上 `notes`，讓讀者知道 MTTD 有工具下限、演練 MTTR 含決策時間。
    """
    return {
        "modes": {
            mode: {
                "sample_count": s.sample_count,
                "mttd_p50_ms": s.mttd_p50_ms,
                "mttd_p95_ms": s.mttd_p95_ms,
                "mttr_p50_ms": s.mttr_p50_ms,
                "mttr_p95_ms": s.mttr_p95_ms,
                "containment_p50_ms": s.containment_p50_ms,
                "containment_p95_ms": s.containment_p95_ms,
                "notes": {"mttd_floor": s.mttd_floor_note, "mttr_mode": s.mttr_mode_note},
            }
            for mode, s in summaries.items()
        }
    }


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
                # #126：畫面二的 Telemetry 欄——每個來源個別的 ✅／❌／—／⏳，
                # 不是引擎已經算好的聚合缺口分類（那個在 `gap` 裡）。
                # `telemetry_mark` 是 purple.console.drilldown 既有的純函數，
                # 這裡是它第一個真的接上的呼叫端。
                "telemetry": [
                    {
                        "source_id": mark.source_id,
                        "state": mark.state.value,
                        "mark": telemetry_mark(
                            mark.state, event_present=mark.event_present
                        ).value,
                    }
                    for mark in result.source_marks
                ],
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
    latency_mode: Callable[[str], str] | None = None,
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
        except CatalogError as exc:
            # `config/scenario-sources.yaml` 目前對真 scenario 刻意留空
            # （WS2 spec §6.2，見 ui/README.md 已知缺口 #6）—— 這不是伺服器故障，
            # 是這個 exercise 的 scenario 還沒在來源清單登記。503 而非未攔截的
            # 500：呼叫端至少能分辨「暫時算不出來」與「程式壞了」，且不洩漏 traceback。
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

    def _latency_assembler(conn) -> LatencyAssembler:
        # mode 來自回應觸發方式（ResponseCommand.triggered_by）；未注入時整場當 exercise
        # （演練預設藍隊觸發＝人在迴圈）。auto 模式的分流屬 #48/#51 整合，見 latency.py。
        mode_of = latency_mode or (lambda _action_id: "exercise")
        return LatencyAssembler(
            executions=ActionExecutionStore(conn),
            events=CoreEventStore(conn),
            mode_of=mode_of,
        )

    @app.get("/api/techniques")
    def list_techniques():
        """白名單的 technique 名稱與**判讀限制**（#26 acceptance criteria）。

        Console 要在每個 technique 旁常駐顯示判讀限制（例如 T1005「僅代表敏感檔
        被開啟，未證明內容外流」），而那段文字的唯一來源是 `config/techniques.yaml`
        —— 在這條端點出現以前它沒有任何 HTTP 出口，前端拿不到。

        由後端吐而不是讓前端自己解析 YAML：白名單是欄位治理的核心（ADR ⑤），
        它的解析規則（層級一致性檢查）住在 `parse_whitelist`，複製一份到前端等於
        讓兩邊對不起來時沒有任何訊號。
        """
        whitelist = default_whitelist()
        return [
            {"id": t.id, "name": t.name, "tactic": t.tactic, "note": t.note}
            for t in whitelist.techniques
        ]

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

    @app.get("/api/exercises/{exercise_id}/battleboard")
    def get_battleboard(exercise_id: str, revealed: bool = False):
        """Battleboard 能吃的**唯一**事件形狀（#82）。

        `purple.battleboard.sanitize` 的純函數在這條端點出現以前沒有任何 HTTP 出口
        —— 規則寫好了卻沒有出口，等於畫面只能自己遮，而前端遮是假的
        （devtools 打開就看到）。所以 sanitization 在這裡發生，回應裡**根本沒有**
        rule／payload／query／secret／ban TTL／internal IP 這些欄位可以外洩。

        `revealed` 是 Q4 的揭露開關，預設 `False`（公開層一律 pending）。
        **它由部署決定，不是由呼叫端決定**：Evaluation Engine 只住 Z-MGMT、
        不對外發佈，瀏覽器只能經 `deploy/ui/nginx.conf` 的 gateway 前綴進來，
        而該設定為公開前綴強制寫死 `revealed=false`、只有 instructor 前綴帶
        `revealed=true`。與服務 token 由 nginx 注入是同一個機制：身分綁在前綴上，
        呼叫端填什麼都無效。
        """

        def _run(conn):
            service = _assembler(conn).build(exercise_id)
            results = list(service.evaluate())
            labels = build_attack_label_map([result.action_id for result in results])
            if revealed:
                events = [
                    project_instructor_event(
                        result, attack_label=labels[result.action_id], team="red"
                    )
                    for result in results
                ]
                return {
                    "revealed": True,
                    "events": [
                        {"attack_label": e.attack_label, "team": e.team, "state": e.state.value}
                        for e in events
                    ],
                }
            public = [
                project_public_event(
                    result,
                    attack_label=labels[result.action_id],
                    team="red",
                    round_ended=False,
                )
                for result in results
            ]
            return {
                "revealed": False,
                "events": [
                    {
                        "attack_label": e.attack_label,
                        "team": e.team,
                        "state": e.state.value,
                        "disclosure": e.disclosure.value,
                    }
                    for e in public
                ],
            }

        return _session(_run)

    @app.post("/api/exercises/{exercise_id}/latency")
    def compute_latency(exercise_id: str):
        """從真實事件組出量測、算 p50/p95、**持久化**，回傳每個 mode 的摘要。

        每個 mode 都必須恰好 `REQUIRED_SAMPLE_COUNT` 筆，否則 `summarize_latency`
        拋 ValueError → 409。少於 20 筆不是「先存著」，是「還不能當最終量測交付」
        （#21 §四）。這條路徑在 #44 的真攻擊面就緒前拿不到足量樣本 —— 那是預期的，
        不是這支程式的缺陷。
        """

        def _run(conn):
            runs = _latency_assembler(conn).build(exercise_id)
            try:
                summaries = summarize_latency(runs)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            LatencySummaryStore(conn).save_all(exercise_id, summaries)
            return render_latency(summaries)

        return _session(_run)

    @app.get("/api/exercises/{exercise_id}/latency")
    def get_latency(exercise_id: str):
        """讀**已持久化**的 latency 摘要。重啟後仍查得到（#90 Phase 4）。

        沒有任何已存摘要時回 `{"modes": {}}`，不是 404 —— 「還沒算過」是合法狀態，
        用 200＋空集合表達，讓前端不必把「沒算」與「查錯」混在同一個錯誤分支。
        """
        return _session(
            lambda conn: render_latency(LatencySummaryStore(conn).for_exercise(exercise_id))
        )

    return app


app = create_app()
