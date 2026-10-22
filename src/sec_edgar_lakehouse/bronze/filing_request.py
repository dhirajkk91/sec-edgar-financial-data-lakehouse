"""All SEC callers share these pacing and retry rules.

A request gets at most three attempts. Temporary failures can retry;
access blocks and long Retry-After delays tell the company run to stop.
The caller is still responsible for checking the downloaded content.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import monotonic, sleep

import httpx


class RequestFailure(Exception):
    """A request error with its attempt count and whether later SEC work must stop."""

    def __init__(self, reason: str, attempts: int, *, stop_run: bool = False) -> None:
        """Keep retry history and the stop signal separate from the error text."""
        super().__init__(reason)
        self.attempts = attempts
        self.stop_run = stop_run


@dataclass(frozen=True, slots=True)
class RequestResult:
    """A successful HTTP response and how many attempts it took."""

    response: httpx.Response
    attempts: int


class RequestPacer:
    """One timing history for the company listing, filing index, documents and retries."""

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        """A supplied clock and sleeper let tests exercise pacing without waiting in real time."""
        self.interval = interval
        self._clock = clock
        self._sleep = sleeper
        self._last_start: float | None = None
        self._last_finish: float | None = None

    def before_request(self, retry_delay: float = 0) -> None:
        """Wait long enough for normal pacing and any delay requested by the SEC."""
        now = self._clock()
        wait = 0.0
        if self._last_start is not None:
            wait = max(wait, self.interval - (now - self._last_start))
        if self._last_finish is not None:
            wait = max(wait, retry_delay - (now - self._last_finish))
        # Both waits run at the same time; use whichever still needs longer.
        if wait > 0:
            self._sleep(wait)
        self._last_start = self._clock()

    def after_request(self) -> None:
        """Remember when the request finished so Retry-After waits start there."""
        self._last_finish = self._clock()


def _utc_now() -> datetime:
    """HTTP-date Retry-After headers need a timezone-aware clock to compare against."""
    return datetime.now(UTC)


def _retry_after(response: httpx.Response, now: Callable[[], datetime]) -> float | None:
    """Read Retry-After as seconds or a dated deadline; ignore headers we cannot use."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        date = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if date.tzinfo is None:
        return None
    return max(0.0, (date - now()).total_seconds())


def request_index_or_document(
    client: httpx.Client,
    url: str,
    user_agent: str,
    *,
    pacer: RequestPacer | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> RequestResult:
    """Make a GET request, using the shared pacer and at most three attempts.

    Only selected temporary failures are retried. A 403, a recognized SEC
    block page or Retry-After over 60 seconds raises RequestFailure with
    stop_run=True. The caller still needs to check the response contents.
    """
    pacer = RequestPacer(0, sleeper=sleep) if pacer is None else pacer
    retry_delay = 0.0
    for attempt in range(1, 4):
        pacer.before_request(retry_delay)
        response: httpx.Response | None = None
        requested_wait: float | None = None
        try:
            response = client.get(
                url,
                headers={"User-Agent": user_agent},
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            pacer.after_request()
            reason = f"{type(exc).__name__}: {exc}"
            if (
                not isinstance(
                    exc,
                    (
                        httpx.NetworkError,
                        httpx.TimeoutException,
                        httpx.RemoteProtocolError,
                    ),
                )
                or attempt == 3
            ):
                raise RequestFailure(reason, attempt) from exc
        else:
            pacer.after_request()
            if response.status_code == 403:
                raise RequestFailure(
                    "HTTP 403 (SEC access forbidden)", attempt, stop_run=True
                )
            # SEC can return its rate-threshold page with 429; honor the status
            # and Retry-After before treating the page as a terminal block.
            if response.status_code != 429 and (
                block_reason := sec_access_block_reason(response.content)
            ):
                raise RequestFailure(block_reason, attempt, stop_run=True)
            if response.is_success:
                return RequestResult(response, attempt)
            reason = f"HTTP {response.status_code}"
            if response.status_code not in {429, 500, 502, 503, 504}:
                raise RequestFailure(reason, attempt)
            if response.status_code in {429, 503}:
                requested_wait = _retry_after(response, now)
                if requested_wait is not None and requested_wait > 60:
                    raise RequestFailure(
                        f"{reason}; SEC Retry-After requires {requested_wait:g} seconds "
                        "(over the 60-second limit)",
                        attempt,
                        stop_run=True,
                    )
            else:
                requested_wait = None
            if attempt == 3:
                raise RequestFailure(reason, attempt)
        # Wait one second before attempt two, then two before attempt three.
        # A longer Retry-After still takes precedence.
        retry_delay = float(2 ** (attempt - 1))
        if response is not None:
            retry_delay = max(retry_delay, requested_wait or 0.0)
    raise AssertionError("The request loop always returns or raises")


def sec_access_block_reason(content: bytes) -> str | None:
    """Recognize SEC block pages without treating ordinary filing text as a block."""
    preview = content[:8192].decode("utf-8", errors="ignore").casefold()
    if "<html" not in preview[:512]:
        return None
    title = re.search(r"<title\b[^>]*>(.*?)</title>", preview, flags=re.DOTALL)
    if title is None:
        return None
    title_text = re.sub(r"<[^>]+>", "", title.group(1)).strip()
    if "sec.gov" in title_text and "request rate threshold exceeded" in title_text:
        return "SEC rate-limit page returned with HTTP 200"
    if "sec.gov" in title_text and (
        "undeclared automated tool" in title_text or "access denied" in title_text
    ):
        return "SEC access-block page returned with HTTP 200"
    if (
        title_text == "access denied"
        and "sec.gov" in preview
        and ("reference #" in preview or "permission to access" in preview)
    ):
        return "SEC access-block page returned with HTTP 200"
    return None
