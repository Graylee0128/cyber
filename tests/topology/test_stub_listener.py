import socket
import threading

from scripts.range.stub_listener import serve


def test_http_date_listener_provides_independent_clock():
    ready = socket.socket()
    ready.bind(("127.0.0.1", 0))
    port = ready.getsockname()[1]
    ready.close()

    threading.Thread(target=serve, args=(port, None, True), daemon=True).start()
    with socket.create_connection(("127.0.0.1", port), timeout=1) as conn:
        conn.sendall(b"GET /ready HTTP/1.1\r\nHost: mgmt-stub\r\n\r\n")
        response = conn.recv(4096)

    assert response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"\r\nDate: " in response
    assert response.endswith(b"\r\n\r\n")
