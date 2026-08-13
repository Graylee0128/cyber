import json

from admission.range_client import HttpRangePublisher


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *_): return None


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
