"""Connectivity reasoning: topology, blast radius, shared upstream, DNS/DHCP, reachability.

handle_bundle(messages, observations, topology) is the whole agent: `messages[0]` is
the TASK, the rest are peer messages. It reads the peer FINDINGs, localises the
faults they name to the deepest link they all depend on, and lists those peers in
`consumed_message_ids`. Without peers it can only judge its own service and path
checks, so the same evidence gives a different conclusion (the --control run).

The topology is a tree: every link runs from `a` (upstream, towards the core) to
`b` (downstream). Missing, stale or malformed checks are unknown, never a pass or
a fail. No thresholds are needed: the checks it owns are booleans.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.protocol.models import (
    Envelope,
    MessageType,
    Observation,
    QualityFlag,
    ResponsePayload,
    Stance,
)

RECIPIENT = "correlation_agent"
UNUSABLE = {QualityFlag.missing.value, QualityFlag.stale.value, QualityFlag.malformed.value,
            QualityFlag.out_of_window.value}
TRUE_VALUES = {"true", "1", "1.0", "yes", "ok", "up", "success"}
FALSE_VALUES = {"false", "0", "0.0", "no", "fail", "failed", "down", "timeout"}
SERVICES = (("dns_success", "dns", "dns_failure"), ("dhcp_success", "dhcp", "dhcp_failure"))


# --------------------------------------------------------------------------- topology


class Topology:
    """Tree view of scenarios/topology.json."""

    def __init__(self, topology: dict[str, Any]) -> None:
        self.nodes = {n["id"]: n for n in topology.get("nodes") or [] if isinstance(n, dict) and "id" in n}
        self.links = {l["link_id"]: l for l in topology.get("links") or [] if isinstance(l, dict) and "link_id" in l}
        self.parent_link = {l["b"]: lid for lid, l in self.links.items()}
        self.children: dict[str, list[str]] = defaultdict(list)
        for link in self.links.values():
            self.children[link["a"]].append(link["b"])

    def knows(self, target: str) -> bool:
        return target in self.nodes or target in self.links

    def upstream(self, target: str) -> list[str]:
        """Links from the target towards the core, nearest first (a link includes itself)."""
        chain: list[str] = []
        link = target if target in self.links else self.parent_link.get(target)
        while link is not None and link not in chain:
            chain.append(link)
            link = self.parent_link.get(self.links[link]["a"])
        return chain

    def common_upstream(self, targets: Iterable[str]) -> Optional[str]:
        """Deepest link every target depends on, or None if they share none."""
        chains = [self.upstream(t) for t in targets]
        if not chains or not all(chains):
            return None
        shared = set(chains[0]).intersection(*chains[1:])
        return next((link for link in chains[0] if link in shared), None)

    def behind(self, link: str) -> list[str]:
        """Blast radius: every device downstream of the link."""
        out: list[str] = []
        stack = [self.links[link]["b"]]
        while stack:
            node = stack.pop()
            if node not in out:
                out.append(node)
                stack.extend(self.children.get(node, []))
        return sorted(out)

    def servers(self, service: str) -> list[str]:
        return sorted(nid for nid, n in self.nodes.items() if service in (n.get("services") or []))


# --------------------------------------------------------------------------- evidence


def as_bool(o: Observation) -> Optional[bool]:
    """A usable boolean check result, or None when unknown."""
    if o.value is None or UNUSABLE.intersection(o.quality_flags):
        return None
    if isinstance(o.value, bool):
        return o.value
    text = str(o.value).strip().lower()
    return True if text in TRUE_VALUES else False if text in FALSE_VALUES else None


def latest_checks(ctx: IncidentContext, metric: str) -> dict[str, Observation]:
    """Latest usable result of a boolean check per device (observations are oldest first)."""
    out: dict[str, Observation] = {}
    for o in ctx.by_metric(metric):
        if o.device_id and as_bool(o) is not None:
            out[o.device_id] = o
    return out


def target_of(hypothesis: str) -> str:
    return hypothesis.split("@", 1)[1] if "@" in hypothesis else ""


def cause_of(hypothesis: str) -> str:
    return hypothesis.split("@", 1)[0]


# --------------------------------------------------------------------------- agent


class ConnectivityAgent(BaseAgent):
    agent_id = "connectivity_agent"
    abbrev = "CONN"

    def handle_bundle(self, messages: list[Envelope], observations: list[Observation],
                      topology: dict[str, Any]) -> list[Envelope]:
        task = messages[0]
        peers = [m for m in messages[1:] if m.type == MessageType.FINDING.value]
        ctx = IncidentContext(incident_id=task.incident_id, clock=task.timestamp, observations=observations,
                              topology=topology, signatures={})
        topo = Topology(topology)
        reach = latest_checks(ctx, "reachability")

        out = self._services(ctx, task, topo, reach)
        out += self._reachability(ctx, task, topo, reach)
        out += self._peers(ctx, task, topo, reach, peers)
        if not any(m.payload["stance"] == Stance.supports.value and cause_of(m.payload["hypothesis"]) != "healthy"
                   for m in out) and not self._peer_faults(topo, peers):
            out += self._healthy(ctx, task, topo)
        if not out:
            answer = (f"no DNS/DHCP/reachability checks and {len(peers)} usable peer finding(s); "
                      "connectivity cannot judge this incident")
            out.append(self.make(ctx, task.sender, MessageType.RESPONSE,
                                 ResponsePayload(answer=answer).model_dump(mode="json"), task.message_id))
        return out

    def _emit(self, ctx: IncidentContext, task: Envelope, **kw: Any) -> Envelope:
        return self.finding(ctx, RECIPIENT, task.message_id, **kw)

    # ----------------------------------------------------------------- DNS / DHCP

    def _services(self, ctx: IncidentContext, task: Envelope, topo: Topology,
                  reach: dict[str, Observation]) -> list[Envelope]:
        out: list[Envelope] = []
        for metric, service, cause in SERVICES:
            checks = latest_checks(ctx, metric)
            servers = topo.servers(service)
            if not checks or not servers:
                continue
            server = servers[0]
            failing = sorted(d for d, o in checks.items() if as_bool(o) is False and d != server)
            passing = sorted(d for d, o in checks.items() if as_bool(o) is True and d != server)
            check_ids = [checks[d].evidence_id for d in failing or passing]
            server_obs = reach.get(server)
            server_up = as_bool(server_obs) if server_obs else None
            server_ids = [server_obs.evidence_id] if server_obs else []

            if failing and server_up is False:
                uplink = topo.parent_link.get(server, server)
                out.append(self._emit(
                    ctx, task, hypothesis=f"upstream_dependency@{uplink}", stance=Stance.contradicts,
                    target_hypothesis=f"{cause}@{server}", evidence_ids=check_ids + server_ids, score=0.7,
                    reason=f"{metric} fails on {', '.join(failing)} but {server} itself is unreachable; "
                           f"the path via {uplink}, not the {service} service, explains it"))
            elif failing:
                state = "reachable" if server_up else "of unknown reachability"
                score = 0.8 if len(failing) >= 2 and server_up else 0.6
                out.append(self._emit(
                    ctx, task, hypothesis=f"{cause}@{server}", stance=Stance.supports,
                    evidence_ids=check_ids + server_ids, score=score,
                    reason=f"{metric} fails on {len(failing)} client(s) ({', '.join(failing)}) "
                           f"while {server} is {state}"))
            elif passing:
                out.append(self._emit(
                    ctx, task, hypothesis=f"healthy@{server}", stance=Stance.contradicts,
                    target_hypothesis=f"{cause}@{server}", evidence_ids=check_ids + server_ids, score=0.7,
                    reason=f"{metric} succeeds on every checked client ({', '.join(passing)})"))
        return out

    # ----------------------------------------------------------------- reachability

    def _reachability(self, ctx: IncidentContext, task: Envelope, topo: Topology,
                      reach: dict[str, Observation]) -> list[Envelope]:
        down = sorted(d for d, o in reach.items() if as_bool(o) is False and topo.knows(d))
        if not down:
            return []
        link = topo.common_upstream(down)
        if link is None:
            return []
        radius = topo.behind(link)
        ids = [reach[d].evidence_id for d in down]
        where, why = link, f"all behind {link}"
        head = topo.links[link]["b"]
        if head not in down and head in reach and as_bool(reach[head]) is True:
            # the device just below the shared link still answers, so the fault is that device
            where, why = head, f"all behind {head}, which itself is reachable"
            ids.append(reach[head].evidence_id)
        return [self._emit(
            ctx, task, hypothesis=f"upstream_dependency@{where}", stance=Stance.supports,
            evidence_ids=ids, score=0.8 if len(down) >= 2 else 0.5,
            reason=f"{len(down)} device(s) unreachable ({', '.join(down)}), {why}; "
                   f"blast radius {len(radius)} device(s): {', '.join(radius)}")]

    # ----------------------------------------------------------------- peer findings

    @staticmethod
    def _peer_faults(topo: Topology, peers: list[Envelope]) -> list[tuple[Envelope, str]]:
        """(peer, target) for every peer finding that names a fault on a topology element."""
        out: list[tuple[Envelope, str]] = []
        for peer in peers:
            hypothesis = peer.payload.get("hypothesis", "")
            target = target_of(hypothesis)
            # supports names a fault; contradicts names one only when it offers a different
            # hypothesis (an alternative). "X contradicts X" just means "not X".
            names_fault = (peer.payload.get("stance") == Stance.supports.value
                           or hypothesis != peer.payload.get("target_hypothesis", hypothesis))
            if names_fault and cause_of(hypothesis) != "healthy" and topo.knows(target):
                out.append((peer, target))
        return out

    def _peers(self, ctx: IncidentContext, task: Envelope, topo: Topology,
               reach: dict[str, Observation], peers: list[Envelope]) -> list[Envelope]:
        faults = self._peer_faults(topo, peers)
        if not faults:
            return []
        shared = topo.common_upstream(t for _, t in faults)
        groups: dict[str, list[tuple[Envelope, str]]] = defaultdict(list)
        for peer, target in faults:     # no shared link: each fault on its own nearest link
            chain = topo.upstream(target)
            if shared or chain:
                groups[shared or chain[0]].append((peer, target))
        out: list[Envelope] = []
        for link in sorted(groups):
            out += self._localise(ctx, task, topo, reach, link, groups[link], shared=shared is not None)
        return out

    def _localise(self, ctx: IncidentContext, task: Envelope, topo: Topology, reach: dict[str, Observation],
                  link: str, group: list[tuple[Envelope, str]], shared: bool) -> list[Envelope]:
        radius = topo.behind(link)
        peer_ids = [e for p, _ in group for e in p.payload.get("evidence_ids", []) if ctx.evidence(e)]
        own_ids = [o.evidence_id for d, o in reach.items() if d in radius]
        if not peer_ids + own_ids:
            return []
        senders = sorted({p.sender for p, _ in group})
        outside = sorted(d for d, o in reach.items() if as_bool(o) is False and d not in radius)
        named = ", ".join(sorted({f"{p.payload['hypothesis']} ({p.sender})" for p, _ in group}))
        score = (0.7 if len(senders) >= 2 else 0.5) if shared else 0.4
        reason = (f"peer findings {named} all depend on {link}; blast radius {len(radius)} device(s): "
                  f"{', '.join(radius)}") if shared else \
                 (f"peer findings {named} sit behind {link}; the peers name faults with no shared upstream, "
                  "so they look independent")
        if outside:
            score = min(score, 0.3)
            reason += f"; but {', '.join(outside)} outside that radius are unreachable too"
        return [self._emit(ctx, task, hypothesis=f"upstream_dependency@{link}", stance=Stance.supports,
                           evidence_ids=peer_ids + own_ids, score=score, reason=reason,
                           consumed_message_ids=sorted(p.message_id for p, _ in group))]

    # ----------------------------------------------------------------- healthy

    def _healthy(self, ctx: IncidentContext, task: Envelope, topo: Topology) -> list[Envelope]:
        """Every own check passes and nothing localises a fault."""
        checks = [o for metric in ("reachability", "dns_success", "dhcp_success")
                  for o in latest_checks(ctx, metric).values()]
        if not checks or any(as_bool(o) is False for o in checks):
            return []
        scope = [t for t in task.payload.get("scope_ids", []) if topo.knows(t)]
        where = topo.common_upstream(scope) if scope else None
        root = next((n for n in sorted(topo.nodes) if n not in topo.parent_link and topo.children.get(n)), None)
        where = where or root
        if where is None:
            return []
        return [self._emit(
            ctx, task, hypothesis=f"healthy@{where}", stance=Stance.supports,
            evidence_ids=[o.evidence_id for o in checks], score=0.5,
            reason=f"all {len(checks)} reachability/DNS/DHCP check(s) pass and no peer finding localises "
                   f"a fault in scope (common upstream {where})")]


def handle_bundle(messages: list[Envelope], observations: list[Observation],
                  topology: dict[str, Any]) -> list[Envelope]:
    """Plug-in contract (docs/PHASE2_TASKS.md D5): messages[0] is the TASK, the rest are peers."""
    return ConnectivityAgent().handle_bundle(messages, observations, topology)
