"""Check the offline guard without attempting an external connection."""

import socket

import httpx
import pytest


@pytest.mark.parametrize("host", ["example.invalid", "192.0.2.1", "2001:db8::1"])
def test_external_dns_is_blocked(host: str) -> None:
    with pytest.raises(AssertionError, match="external host"):
        socket.getaddrinfo(host, 443)


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_external_connection_is_blocked(method: str) -> None:
    with (
        socket.socket() as client,
        pytest.raises(AssertionError, match="external host"),
    ):
        getattr(client, method)(("192.0.2.1", 443))


def test_external_datagram_is_blocked() -> None:
    with (
        socket.socket(type=socket.SOCK_DGRAM) as client,
        pytest.raises(AssertionError, match="external host"),
    ):
        client.sendto(b"offline", ("192.0.2.1", 443))


def test_loopback_connection_is_allowed() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname(), timeout=2) as client:
            connection, _ = listener.accept()
            with connection:
                client.sendall(b"local")
                assert connection.recv(5) == b"local"


def test_mocked_external_http_is_allowed() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="mock"))
    with httpx.Client(transport=transport) as client:
        assert client.get("https://example.invalid").text == "mock"
