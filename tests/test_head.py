"""HEAD requests are answered like GET, without a body."""

from fastapi.testclient import TestClient

from piclstats.web.app import app


def test_head_on_a_get_route():
    client = TestClient(app)
    get = client.get("/login")
    head = client.head("/login")
    assert get.status_code == 200
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"] == get.headers["content-type"]
