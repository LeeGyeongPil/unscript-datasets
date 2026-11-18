"""Block every network connection so the whole suite proves it runs offline."""

import socket

import pytest


@pytest.fixture(autouse=True)
def _block_sockets(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("network access is blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture(autouse=True)
def _neutral_locale(monkeypatch):
    """Tests must not depend on the developer's locale; they pick languages explicitly."""
    for name in ("UNSCRIPT_DATASETS_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
