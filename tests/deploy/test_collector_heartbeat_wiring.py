"""Golden target emits three independently labelled collector heartbeat streams."""

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_target_app():
    path = ROOT / "deploy" / "range-target" / "app.py"
    spec = importlib.util.spec_from_file_location("range_target_app", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_target_app_canary_is_an_alloy_end_to_end_heartbeat(tmp_path, monkeypatch):
    app = _load_target_app()
    path = tmp_path / "app.log"
    monkeypatch.setattr(app, "LOG_PATH", str(path))
    monkeypatch.setattr(app, "_now", lambda: "2026-08-11T06:30:00+00:00")

    app.emit_alloy_heartbeat()

    [line] = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(line) == {
        "ts": "2026-08-11T06:30:00+00:00",
        "app": "range-target",
        "event": "alloy.heartbeat",
    }


def test_falco_native_metrics_emit_every_30_seconds_to_existing_file_output():
    bake = (ROOT / "deploy" / "range-target" / "bake.sh").read_text(encoding="utf-8")

    assert "metrics:" in bake
    assert "enabled: true" in bake
    assert "interval: 30s" in bake
    assert "output_rule: true" in bake


def test_alloy_tails_response_agent_heartbeat_as_its_own_stream():
    alloy = (ROOT / "deploy" / "range-target" / "config.alloy").read_text(encoding="utf-8")

    assert "/var/log/purplescope/response-agent.jsonl" in alloy
    assert 'job      = "response-agent"' in alloy
