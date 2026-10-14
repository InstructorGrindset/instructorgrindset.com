"""Small stdlib HTTP layer: JSON and binary GET with retry, backoff and blocked network detection.

Fetchers call :func:`get_json`, :func:`get_bytes` or :func:`download` through this module (``http.get_json``) so
tests can monkeypatch one function and run the whole ETL offline.
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

USER_AGENT = "instructorgrindset-career-map-climate-etl/1.0 (+https://instructorgrindset.com/map/)"

# Indirection so tests can replace sleeping.
_sleep: Callable[[float], None] = time.sleep


class NetworkBlocked(RuntimeError):
    """The host could not be reached at all (DNS failure, proxy refusal, no route)."""


class HttpStatusError(RuntimeError):
    def __init__(self, url: str, status: int, body: str):
        url = redact(url)
        super().__init__(f"HTTP {status} from {url}: {redact(body[:300])}")
        self.url = url
        self.status = status
        self.body = body


class RateLimited(RuntimeError):
    """The API refused the request for rate limit reasons."""

    def __init__(self, message: str, daily: bool = False):
        super().__init__(message)
        self.daily = daily


_SECRET_PARAMS = re.compile(r"(\bapikey=)[^&#\s\"']*", re.IGNORECASE)


def redact(text: str) -> str:
    """Hide API keys in URLs before they reach messages or logs."""
    return _SECRET_PARAMS.sub(r"\1REDACTED", text)


def build_url(url: str, params: Optional[Mapping[str, Any]] = None) -> str:
    if not params:
        return url
    clean = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        clean[k] = v
    return f"{url}?{urllib.parse.urlencode(clean, safe=',:*')}"


# Errors that mean the connection dropped, not that the host is unreachable.
_TRANSIENT = (socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, http.client.IncompleteRead)


def _sleep_seconds(attempt: int, backoff: float, retry_after: Optional[str]) -> float:
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    return backoff * (2**attempt)


def get_json(
    url: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    timeout: float = 120.0,
    retries: int = 4,
    backoff: float = 5.0,
) -> Any:
    """GET ``url`` and decode JSON.

    Retries 429 and 5xx responses and timeouts with exponential backoff.
    Raises :class:`NetworkBlocked` when the host cannot be reached,
    :class:`RateLimited` when 429 persists (``daily=True`` for a daily cap),
    and :class:`HttpStatusError` for other HTTP errors.
    """
    return _get(url, params, "application/json", lambda resp: json.loads(resp.read().decode("utf-8")),
                timeout=timeout, retries=retries, backoff=backoff)


def get_bytes(
    url: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    accept: str = "*/*",
    timeout: float = 120.0,
    retries: int = 4,
    backoff: float = 5.0,
) -> bytes:
    """GET ``url`` and return the body, with the same retries and errors as :func:`get_json`."""
    return _get(url, params, accept, lambda resp: resp.read(), timeout=timeout, retries=retries, backoff=backoff)


def download(
    url: str,
    dest: Path,
    *,
    timeout: float = 600.0,
    retries: int = 4,
    backoff: float = 5.0,
) -> Path:
    """Stream ``url`` into ``dest`` (written to ``dest.part`` first, then renamed), with the same retries."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    def save(resp: Any) -> Path:
        with part.open("wb") as fh:
            shutil.copyfileobj(resp, fh, 1 << 20)
        part.replace(dest)
        return dest

    return _get(url, None, "*/*", save, timeout=timeout, retries=retries, backoff=backoff)


def _get(url: str, params: Optional[Mapping[str, Any]], accept: str, handle: Callable[[Any], Any], *,
         timeout: float, retries: int, backoff: float) -> Any:
    full = build_url(url, params)
    # Generic User-Agent naming the project and its public page. Never an email address.
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": accept})
    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return handle(resp)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace") if e.fp else ""
            if e.code == 429:
                reason = _reason(body)
                if "daily" in reason.lower():
                    raise RateLimited(f"Daily API limit reached: {reason}", daily=True) from e
                last_error = RateLimited(f"Rate limited: {reason}")
                wait = 65.0 if "minut" in reason.lower() else _sleep_seconds(attempt, backoff, e.headers.get("Retry-After"))
            elif 500 <= e.code < 600:
                last_error = HttpStatusError(full, e.code, body)
                wait = _sleep_seconds(attempt, backoff, e.headers.get("Retry-After"))
            else:
                raise HttpStatusError(full, e.code, body) from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, _TRANSIENT):
                last_error = e
                wait = _sleep_seconds(attempt, backoff, None)
            else:
                # DNS failure, refused tunnel, no route: the network does not allow this host.
                raise NetworkBlocked(f"cannot reach {urllib.parse.urlsplit(full).netloc}: {e.reason}") from e
        except _TRANSIENT as e:
            # timeouts and transfers cut off mid response (proxy or upstream drops) are retried
            last_error = e
            wait = _sleep_seconds(attempt, backoff, None)
        if attempt < retries:
            _sleep(wait)
    if isinstance(last_error, RuntimeError):
        raise last_error
    raise HttpStatusError(full, 0, f"giving up after {retries + 1} attempts: {redact(str(last_error))}")


def _reason(body: str) -> str:
    try:
        data = json.loads(body)
        if isinstance(data, dict) and data.get("reason"):
            return redact(str(data["reason"]))
    except ValueError:
        pass
    return redact(body[:200])
