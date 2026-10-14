"""Retry classification in the climate HTTP layer (no network)."""
import io
import json
import ssl
import urllib.error

import pytest

from pipeline.climate import http as chttp


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run(monkeypatch, outcomes):
    calls = iter(outcomes)

    def fake_urlopen(req, timeout=None):
        o = next(calls)
        if isinstance(o, BaseException):
            raise o
        return _Resp(json.dumps(o).encode())

    monkeypatch.setattr(chttp.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(chttp, "_sleep", lambda s: None)
    return chttp.get_json("https://example.org/x", retries=3)


@pytest.mark.parametrize("err", [
    ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
    urllib.error.URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol")),
    ConnectionResetError("reset"),
    urllib.error.URLError(ConnectionResetError("reset")),
])
def test_dropped_transfer_is_retried(monkeypatch, err):
    assert _run(monkeypatch, [err, {"ok": 1}]) == {"ok": 1}


def test_unreachable_host_is_blocked(monkeypatch):
    with pytest.raises(chttp.NetworkBlocked):
        _run(monkeypatch, [urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))])
