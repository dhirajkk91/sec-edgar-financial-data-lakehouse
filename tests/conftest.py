"""Keep tests offline while allowing local servers and mocked HTTP transports."""

import ipaddress
import socket
from typing import Any

import pytest


def _require_loopback(host: str | bytes | None) -> None:
    if host is None or host in ("localhost", b"localhost"):
        return
    text = host.decode("ascii") if isinstance(host, bytes) else host
    try:
        if ipaddress.ip_address(text).is_loopback:
            return
    except ValueError:
        pass
    raise AssertionError(f"Tests must not contact an external host: {text}")


@pytest.fixture(autouse=True)
def offline_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block DNS and socket connections before any real HTTP request can leave."""
    original_resolve = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto

    def resolve(host: Any, *args: Any, **kwargs: Any) -> Any:
        _require_loopback(host)
        return original_resolve(host, *args, **kwargs)

    def check(sock: socket.socket, address: Any) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            _require_loopback(address[0])

    def connect(sock: socket.socket, address: Any) -> None:
        check(sock, address)
        original_connect(sock, address)

    def connect_ex(sock: socket.socket, address: Any) -> int:
        check(sock, address)
        return original_connect_ex(sock, address)

    def sendto(sock: socket.socket, data: bytes, *args: Any) -> int:
        check(sock, args[-1])
        return original_sendto(sock, data, *args)

    # Patch sockets, not HTTP clients: MockTransport never opens a socket.
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", sendto)
