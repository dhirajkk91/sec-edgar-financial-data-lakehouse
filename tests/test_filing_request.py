from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from sec_edgar_lakehouse.filing_request import (
    RequestFailure,
    RequestPacer,
    request_index_or_document,
)

URL = "https://www.sec.gov/Archives/edgar/data/1122304/000119312515118890/report.htm"
USER_AGENT = "RequestTests contact@example.org"
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


class ControlledTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds

    def pacer(self, interval: float = 0.5) -> RequestPacer:
        return RequestPacer(interval, clock=self.clock, sleeper=self.sleep)


@pytest.mark.parametrize(
    "first", [429, 500, 502, 503, 504, "connect", "timeout", "reset"]
)
def test_transient_failure_recovers_with_one_second_wait(first: int | str) -> None:
    time = ControlledTime()
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            if first == "connect":
                raise httpx.ConnectError("offline", request=request)
            if first == "timeout":
                raise httpx.ReadTimeout("slow", request=request)
            if first == "reset":
                raise httpx.RemoteProtocolError("reset", request=request)
            assert isinstance(first, int)
            return httpx.Response(first)
        return httpx.Response(200, content=b"recovered")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = request_index_or_document(client, URL, USER_AGENT, pacer=time.pacer())
    assert result.attempts == 2
    assert result.response.content == b"recovered"
    assert calls == 2
    assert time.sleeps == [1]


def test_exhausted_transient_failure_has_three_attempts_and_final_reason() -> None:
    time = ControlledTime()
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, content=b"unavailable")

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(RequestFailure, match="HTTP 503") as caught,
    ):
        request_index_or_document(client, URL, USER_AGENT, pacer=time.pacer())
    assert calls == caught.value.attempts == 3
    assert time.sleeps == [1, 2]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("5", 5),
        (format_datetime(NOW + timedelta(seconds=7)), 7),
        ("not-a-date", 1),
    ],
)
def test_retry_after_uses_longer_wait(value: str, expected: float) -> None:
    time = ControlledTime()
    replies = iter(
        [httpx.Response(429, headers={"Retry-After": value}), httpx.Response(200)]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as client:
        result = request_index_or_document(
            client, URL, USER_AGENT, pacer=time.pacer(), now=lambda: NOW
        )
    assert result.attempts == 2
    assert time.sleeps == [expected]


def test_429_rate_threshold_page_honors_retry_after_and_recovers() -> None:
    time = ControlledTime()
    calls = 0
    block_page = (
        b"<html><head><title>SEC.gov | Request Rate Threshold Exceeded"
        b"</title></head><body>Slow down</body></html>"
    )

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "5"}, content=block_page)
        return httpx.Response(200, content=b"recovered")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = request_index_or_document(client, URL, USER_AGENT, pacer=time.pacer())

    assert calls == result.attempts == 2
    assert time.sleeps == [5]
    assert result.response.content == b"recovered"


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("value", ["61", format_datetime(NOW + timedelta(seconds=61))])
def test_retry_after_over_sixty_stops_without_early_request(
    status: int, value: str
) -> None:
    time = ControlledTime()
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers={"Retry-After": value})

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(RequestFailure, match="over the 60-second limit") as caught,
    ):
        request_index_or_document(
            client, URL, USER_AGENT, pacer=time.pacer(), now=lambda: NOW
        )
    assert caught.value.stop_run
    assert caught.value.attempts == calls == 1
    assert time.sleeps == []


def test_retry_after_on_other_status_does_not_change_wait() -> None:
    time = ControlledTime()
    replies = iter(
        [httpx.Response(500, headers={"Retry-After": "30"}), httpx.Response(200)]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as client:
        request_index_or_document(client, URL, USER_AGENT, pacer=time.pacer())
    assert time.sleeps == [1]


@pytest.mark.parametrize("status", [301, 400, 404])
def test_other_status_is_attempted_once(status: int) -> None:
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(RequestFailure, match=f"HTTP {status}") as caught,
    ):
        request_index_or_document(
            client, URL, USER_AGENT, pacer=ControlledTime().pacer()
        )
    assert calls == caught.value.attempts == 1
