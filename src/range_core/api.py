"""HTTP interface for Cyber Range Core.

WS5 uses FastAPI rather than P1's intentionally tiny stdlib HTTP shell.  The
Core interface already has nine planned endpoints and #36 adds resumable SSE;
FastAPI provides typed validation and OpenAPI while retaining Starlette's
streaming response support for that later ticket.

Only ``GET /api/scenarios`` belongs to issue #31.  Exercise lifecycle, scoring,
actions, events, and SSE remain outside this module until their own tickets.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from range_core.scenarios import Scenario, ScenarioCatalog

DEFAULT_SCENARIO_DIRECTORY = Path(__file__).resolve().parents[2] / "scenarios"


def create_app(catalog: ScenarioCatalog | None = None) -> FastAPI:
    """Create the WS5 application with an injected or on-disk catalog."""

    loaded_catalog = catalog or ScenarioCatalog.from_directory(
        DEFAULT_SCENARIO_DIRECTORY
    )
    application = FastAPI(title="Cyber Range Core")

    @application.get("/api/scenarios", response_model=list[Scenario])
    def list_scenarios() -> list[Scenario]:
        return list(loaded_catalog.scenarios)

    return application


app = create_app()
