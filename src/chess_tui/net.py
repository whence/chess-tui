"""HTTP client for the chess-tui network player API.

The wire format is described in ``openapi/chess-tui-net.yaml``. The client
here is a thin stdlib-only wrapper around ``urllib.request`` so we don't
have to add ``httpx``/``requests`` as a dep.
"""

from __future__ import annotations

import http.client
import json
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 10.0


class NetworkError(RuntimeError):
    """Base class for failures talking to a network player server."""


class ServerError(NetworkError):
    """The server returned a non-2xx response.

    ``status`` is the HTTP status code; ``body`` is the raw response body
    (parsed as JSON if possible).
    """

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        msg = f"server returned HTTP {status}"
        if isinstance(body, dict) and "error" in body:
            msg += f": {body['error']}"
        super().__init__(msg)


class TransportError(NetworkError):
    """Connection refused, DNS failure, timeout, etc."""


class ServerBusyError(NetworkError):
    """The server is busy processing another request (HTTP 503)."""


def _post_json(url: str, payload: dict, *, timeout: float) -> dict:
    """POST a JSON payload, return the parsed JSON response.

    Raises :class:`ServerError` for non-2xx responses, :class:`TransportError`
    for connection / timeout problems.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        # Non-2xx — try to parse the body for an error message.
        body = exc.read()
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            body = {"error": body.decode("utf-8", errors="replace")}
        raise ServerError(exc.code, body) from None
    except urllib.error.URLError as exc:
        raise TransportError(f"could not reach {url}: {exc.reason}") from exc
    except (http.client.RemoteDisconnected, ConnectionError, OSError) as exc:
        # RemoteDisconnected: server closed connection mid-request
        # ConnectionError: connection refused, reset, etc.
        # OSError: broken pipe, connection reset, etc.
        raise TransportError(f"could not reach {url}: {exc}") from exc
    except TimeoutError as exc:
        raise TransportError(f"timed out reaching {url} after {timeout}s") from exc

    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ServerError(200, raw.decode("utf-8", errors="replace")) from exc


def request_move(
    url: str,
    fen: str,
    *,
    moves: list[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """POST ``{"fen": fen, "moves": [...]}`` to ``{url}/move`` and return the SAN string.

    Raises :class:`ServerError` (status >= 400), :class:`TransportError`
    (connection / timeout), or :class:`ServerBusyError` (503).
    The returned SAN is not validated against legality here — callers
    do that against their own board.
    """
    body: dict = {"fen": fen}
    if moves is not None:
        body["moves"] = moves
    try:
        payload = _post_json(f"{url.rstrip('/')}/move", body, timeout=timeout)
    except ServerError as exc:
        if exc.status == 503:
            raise ServerBusyError(f"server busy: {exc.body}") from exc
        raise
    san = payload.get("san")
    if not isinstance(san, str):
        raise ServerError(200, payload)
    return san


# Generous default — observers (e.g. chess-tui-engine) may legitimately
# take a while to "think" before responding. The TUI never awaits the
# response, so this only caps how long a stuck observer can keep a
# background thread alive.
OBSERVER_TIMEOUT = 30.0


def post_observer(
    url: str,
    fen: str,
    *,
    moves: list[str] | None = None,
    timeout: float = OBSERVER_TIMEOUT,
    quiet: bool = False,
) -> None:
    """Fire-and-forget POST to an observer. Reads and discards the response.

    Observers are just regular chess-tui network player servers
    (``chess-tui-engine``, ``chess-tui-nova``, ``chess-tui-maia``, etc.)
    that happen to be listening. The TUI does not parse or use their
    response — the observer will compute a move and print it on its own
    stdout, while the TUI ignores it.

    All errors are swallowed:

    - ``TransportError`` (connection refused, timeout, DNS failure, etc.)
    - ``ServerError`` (4xx / 5xx responses, including 503 busy)
    - any other unexpected exception (e.g. malformed server reply)

    By default, failures are logged to stderr as a one-liner so silent
    network errors (e.g. an observer bound to ``127.0.0.1`` on a remote
    machine that can't be reached) are visible. Set ``quiet=True`` to
    suppress the log (used by tests, and by callers that prefer silence).

    The function always returns ``None``. It is intentionally synchronous
    so callers can run it via ``loop.run_in_executor`` from async code
    without blocking the event loop.
    """
    body: dict = {"fen": fen}
    if moves is not None:
        body["moves"] = moves
    try:
        _post_json(f"{url.rstrip('/')}/move", body, timeout=timeout)
    except NetworkError as exc:
        if not quiet:
            print(
                f"[chess-tui observer] {url}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        return None
    except Exception as exc:
        if not quiet:
            print(
                f"[chess-tui observer] {url}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        return None
    return None
