"""HTTP client for the Connectivity agent process (connectivity_service/app.py).

send_bundle posts the request Bundle to `<endpoint>/messages` and returns a
ConnectivityReply with the raw reply messages (the bus validates each one), the
raw request and response bodies for the trace, and the HTTP status. It never
raises: an unreachable or silent agent becomes `error = "AGENT_TIMEOUT: ..."`,
anything else that goes wrong becomes `error = "AGENT_FAILURE: ..."`.

Connection errors, timeouts and 502/503/504 are retried once. A 4xx or a 500 is
the agent's considered answer (a typed ERROR), so it is not retried.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from netcoop.orchestrator.transport import ConnectivityReply
from netcoop.protocol.models import Bundle

DEFAULT_TIMEOUT_S = 5.0
DEFAULT_RETRIES = 1
RETRY_STATUSES = {502, 503, 504}


def send_bundle(bundle: Bundle, endpoint: str, *, timeout: float = DEFAULT_TIMEOUT_S,
                retries: int = DEFAULT_RETRIES, client: Optional[httpx.Client] = None) -> ConnectivityReply:
    """POST the Bundle. `client` lets tests pass an httpx test client or mock transport;
    the timeout is set on the client this function creates."""
    request = bundle.model_dump(mode="json")
    url = endpoint.rstrip("/") + "/messages"
    try:
        if client is not None:
            return _send(client, url, request, timeout, retries)
        with httpx.Client(timeout=timeout) as own:
            return _send(own, url, request, timeout, retries)
    except Exception as exc:  # the orchestrator must always get a reply object
        return ConnectivityReply(request_body=request, error=f"AGENT_FAILURE: {type(exc).__name__}: {exc}")


def _send(client: httpx.Client, url: str, request: dict[str, Any], timeout: float, retries: int) -> ConnectivityReply:
    attempts = 1 + max(retries, 0)
    failure = ""
    for attempt in range(1, attempts + 1):
        try:
            response = client.post(url, json=request)
        except httpx.TimeoutException as exc:
            failure = f"no reply from {url} within {timeout:g}s ({type(exc).__name__})"
            continue
        except httpx.TransportError as exc:
            failure = f"cannot reach {url} ({type(exc).__name__}: {exc})"
            continue
        if response.status_code in RETRY_STATUSES and attempt < attempts:
            continue
        return _reply(request, response)
    return ConnectivityReply(request_body=request,
                             error=f"AGENT_TIMEOUT: connectivity_unreachable: {failure} after {attempts} attempt(s)")


def _reply(request: dict[str, Any], response: httpx.Response) -> ConnectivityReply:
    """Keep whatever came back for the trace; hand over messages only from a Bundle-shaped body."""
    status = response.status_code
    try:
        body: Any = response.json()
    except ValueError:
        return ConnectivityReply(request_body=request, response_body=response.text, http_status=status,
                                 error=f"AGENT_FAILURE: HTTP {status}: reply is not JSON")
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list):
        return ConnectivityReply(request_body=request, response_body=body, http_status=status,
                                 error=f"AGENT_FAILURE: HTTP {status}: reply is not a Bundle (no 'messages' list)")
    error = None if 200 <= status < 300 else f"AGENT_FAILURE: HTTP {status}: {_first_error(messages)}"
    return ConnectivityReply(messages=messages, request_body=request, response_body=body,
                             http_status=status, error=error)


def _first_error(messages: list[Any]) -> str:
    for m in messages:
        payload = m.get("payload") if isinstance(m, dict) else None
        if isinstance(m, dict) and m.get("type") == "ERROR" and isinstance(payload, dict):
            return f"{payload.get('code')}: {payload.get('detail')}"
    return "agent rejected the Bundle"
