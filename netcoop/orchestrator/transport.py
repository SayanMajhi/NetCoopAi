"""How the orchestrator reaches the Connectivity agent.

A transport takes the request Bundle and returns a ConnectivityReply. The remote
transport (netcoop/orchestrator/remote.py) posts it over HTTP; the local one calls
connectivity_service.logic in-process (tests only). Both keep the raw request and
response bodies so the trace proves what crossed the wire.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from netcoop.protocol.models import Bundle, Envelope


@dataclass
class ConnectivityReply:
    messages: list[Any] = field(default_factory=list)        # raw reply messages; the bus validates each one
    request_body: dict[str, Any] = field(default_factory=dict)
    response_body: Optional[Any] = None                      # raw JSON as received (None if no answer)
    http_status: Optional[int] = None                        # None for in-process calls
    error: Optional[str] = None                              # set when the agent could not be reached or failed


Transport = Callable[[Bundle], ConnectivityReply]

HandleBundle = Callable[..., list[Envelope]]  # handle_bundle(messages, observations, topology)


def local_transport(handle_bundle: HandleBundle) -> Transport:
    """Wrap connectivity_service.logic.handle_bundle as an in-process transport."""

    def send(bundle: Bundle) -> ConnectivityReply:
        request = bundle.model_dump(mode="json")
        try:
            replies = handle_bundle(bundle.messages, bundle.observations, bundle.topology)
        except Exception as exc:  # the agent's bug must not crash the pipeline
            return ConnectivityReply(request_body=request, error=f"AGENT_FAILURE: {type(exc).__name__}: {exc}")
        raw = [m.model_dump(mode="json") for m in replies]
        return ConnectivityReply(messages=raw, request_body=request, response_body={"messages": raw})

    return send
