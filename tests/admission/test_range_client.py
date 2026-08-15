import json

from admission.range_client import HttpRangePublisher


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self): return b"{}"


class PrepareResponse:
    status = 201
    def __init__(self, body): self._body = body
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self): return json.dumps(self._body).encode()


def test_http_publisher_matches_range_core_contract(monkeypatch):
    requests = []
    monkeypatch.setattr("admission.range_client.urlopen", lambda request, timeout: requests.append(request) or Response())
    client = HttpRangePublisher("https://range", "token")
    client.publish_player(exercise_id="EX", player_id="P1", team="red", source_ip="10.167.30.11")
    client.publish_player(exercise_id="EX", player_id="P2", team="blue", source_ip=None)
    client.revoke_player("EX", "P1")
    assert requests[0].method == "PUT"
    assert json.loads(requests[0].data) == {"team": "red", "source_ip": "10.167.30.11"}
    assert json.loads(requests[1].data) == {"team": "blue"}
    assert requests[2].method == "DELETE"
    assert requests[0].headers["Authorization"] == "Bearer token"


def test_prepare_posts_scenario_id_and_returns_range_core_body(monkeypatch):
    # #143 項目 1：exercise_id 由 Range Core 決定，這裡驗證原樣回傳、不改寫。
    body = {"exercise_id": "ex-abc123", "scenario_id": "scn-1", "state": "prepared"}
    requests = []
    monkeypatch.setattr(
        "admission.range_client.urlopen",
        lambda request, timeout: requests.append(request) or PrepareResponse(body),
    )
    client = HttpRangePublisher("https://range", "admission-token")
    result = client.prepare("scn-1")
    assert requests[0].method == "POST"
    assert requests[0].get_full_url() == "https://range/api/exercises/prepare"
    assert json.loads(requests[0].data) == {"scenario_id": "scn-1"}
    assert requests[0].headers["Authorization"] == "Bearer admission-token"
    assert result == body
