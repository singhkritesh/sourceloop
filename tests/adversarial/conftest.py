"""Adversarial suite fixtures: every test here is marked `adversarial`, runs offline and deterministically."""
import socket

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/adversarial/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.adversarial)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real socket connect or DNS lookup during an adversarial test is itself a failure (SSRF / egress)."""

    def boom(*a, **k):
        raise AssertionError("network access attempted in an adversarial test")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket.socket, "connect_ex", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
