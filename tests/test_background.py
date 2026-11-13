"""Background running: quit endpoint, start-at-login item, and a real start/stop cycle."""

import os
import sys

import pytest
from fastapi.testclient import TestClient

from rsvp import background, server


@pytest.fixture
def client(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_exit_soon", lambda *a, **k: calls.append(True))
    c = TestClient(server.create_app(tmp_path / "lib", inbox=tmp_path / "inbox"))
    c.exit_calls = calls
    return c


def test_quit_requires_the_pages_header(client):
    assert client.post("/api/shutdown").status_code == 403       # e.g. another website
    assert client.exit_calls == []
    r = client.post("/api/shutdown", headers=background.SHUTDOWN_HEADER)
    assert r.status_code == 200 and client.exit_calls == [True]


@pytest.mark.skipif(not background.autostart_supported(), reason="Windows/macOS only")
def test_start_at_login_toggle(client, tmp_path, monkeypatch):
    monkeypatch.setenv("RSVP_STARTUP_DIR", str(tmp_path / "startup"))
    assert client.get("/api/autostart").json() == {"supported": True, "enabled": False}
    assert client.put("/api/autostart", json={"enabled": True}).status_code == 403  # header required
    on = client.put("/api/autostart", json={"enabled": True}, headers=background.SHUTDOWN_HEADER).json()
    assert on["enabled"] is True
    item = tmp_path / "startup" / (background.SHORTCUT_NAME if os.name == "nt" else f"{background.APP_LABEL}.plist")
    assert item.exists()
    off = client.put("/api/autostart", json={"enabled": False}, headers=background.SHUTDOWN_HEADER).json()
    assert off["enabled"] is False and not item.exists()


def test_server_runs_without_a_console(tmp_path, monkeypatch):
    # pythonw / login items have no stdout; serve() must not crash on startup output.
    monkeypatch.setenv("RSVP_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(background, "is_running", lambda *a, **k: True)  # takes the "already running" path
    server.serve(port=8796)
    assert "already running" in (tmp_path / "server.log").read_text(encoding="utf-8")


def test_background_start_and_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("RSVP_STATE_DIR", str(tmp_path / "state"))
    port, lib = 8797, tmp_path / "lib"
    url = background.url_for("127.0.0.1", port)
    assert not background.is_running(url)
    try:
        assert background.start(port=port, library=lib, open_browser=False, wait=60, log=lambda *_: None) == 0
        assert background.is_running(url)
        # starting again just reports it's already running
        msgs = []
        assert background.start(port=port, library=lib, open_browser=False, log=msgs.append) == 0
        assert "already running" in msgs[0]
    finally:
        assert background.stop(port=port, log=lambda *_: None) == 0
    assert not background.is_running(url)
    assert "RSVP Reader on" in (tmp_path / "state" / "server.log").read_text(encoding="utf-8")
