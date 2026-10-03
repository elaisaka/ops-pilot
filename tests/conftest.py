"""All ordinary acceptance tests are offline; no credential values are supplied."""
import socket

import pytest
from fastapi.testclient import TestClient
from app.auth import create_demo_credentials


@pytest.fixture(autouse=True)
def demo_credentials(tmp_path, monkeypatch):
    """Legacy API regressions use a real, random authenticated supervisor."""
    path, tokens = create_demo_credentials(tmp_path / "auth")
    monkeypatch.setenv("DEMO_AUTH_FILE", str(path))
    original = TestClient.__init__
    def authenticated(self, *args, **kwargs):
        headers = {"Authorization": "Bearer " + tokens["supervisor"], **kwargs.pop("headers", {})}
        original(self, *args, headers=headers, **kwargs)
    monkeypatch.setattr(TestClient, "__init__", authenticated)
    return tokens


@pytest.fixture(autouse=True)
def no_model_network_or_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    original_connect = socket.socket.connect
    def allow_event_loop_socketpair(sock, address):
        # Windows asyncio implements socketpair using a local loopback connection.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        raise AssertionError("External network access is disabled in offline tests")
    def blocked(*args, **kwargs):
        raise AssertionError("Network access is disabled in offline tests")
    monkeypatch.setattr(socket.socket, "connect", allow_event_loop_socketpair)
    monkeypatch.setattr(socket, "create_connection", blocked)
