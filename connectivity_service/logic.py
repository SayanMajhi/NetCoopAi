"""Connectivity reasoning: topology, blast radius, shared upstream, DNS/DHCP, reachability.

handle_bundle(messages, observations, topology) is the whole agent: `messages[0]` is
the TASK, the rest are peer messages. It reads the peer FINDINGs, localises the
faults they name to the deepest link they all depend on, and lists those peers in
`consumed_message_ids`. Without peers it can only judge its own service and path
checks, so the same evidence gives a different conclusion (the --control run).

What it may claim (Phase 2 review, R1/R2):
- Only from its OWN evidence (reachability / DNS / DHCP checks, and link loss for an
  AP uplink). It never re-cites a peer's evidence IDs; peers are referenced through
  `consumed_message_ids`.
- Scope: for a fault a peer names, it adds the blast radius and how its own checks in
  that radius look (a supports finding on the peer's hypothesis).
- `upstream_dependency@L` only when >= 2 distinct fault targets share link L and L is
  not itself one of them (a genuinely different explanation).
- Health only for what it measured: `healthy@<link> contradicts upstream_dependency@<link>`
  when every device behind the link answered, and never when any observation in the
  Bundle is missing or stale. It never claims a whole campus or zone is healthy.

The topology is a tree: every link runs from `a` (upstream, towards the core) to
`b` (downstream). Missing, stale or malformed checks are unknown, never a pass or
a fail. The only threshold it reads is packet_loss_pct `warn`, for AP uplinks.
"""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

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
OWN_CHECKS = ("reachability", "dns_success", "dhcp_success")
INCOMPLETE = {QualityFlag.missing.value, QualityFlag.stale.value, QualityFlag.out_of_window.value}
SIGNATURES = Path(__file__).resolve().parents[1] / "netcoop" / "config" / "signatures-1.0.yaml"


@lru_cache(maxsize=1)
def loss_warn() -> Optional[float]:
    """packet_loss_pct warn from the versioned signatures config (thresholds live only there)."""
    config = yaml.safe_load(SIGNATURES.read_text(encoding="utf-8"))
    return (config.get("thresholds") or {}).get("packet_loss_pct", {}).get("warn")


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

    def radius(self, target: str) -> list[str]:
        """Devices affected by a fault on `target`: behind a link, or a device and its descendants."""
        if target in self.links:
            return self.behind(target)
        out: list[str] = []
        stack = [target]
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
        faults = self._peer_faults(topo, peers)
        notes: list[str] = []

        out = self._services(ctx, task, topo, reach)
        out += self._shared_upstream(ctx, task, topo, reach, faults)
        out += self._scope(ctx, task, topo, faults, notes)
        out += self._wifi_uplinks(ctx, task, topo, reach, faults, notes)
        out += self._path_health(ctx, task, topo, reach, faults, {m.payload["hypothesis"] for m in out})
        if notes or not out:
            answer = "; ".join(notes) if notes else (
                f"no DNS/DHCP/reachability checks and {len(peers)} usable peer finding(s); "
                "connectivity cannot judge this incident")
            out.append(self.make(ctx, task.sender, MessageType.RESPONSE,
                                 ResponsePayload(answer=answer).model_dump(mode="json"), task.message_id))
        return out

    def _emit(self, ctx: IncidentContext, task: Envelope, **kw: Any) -> Envelope:
        return self.finding(ctx, RECIPIENT, task.message_id, **kw)

    @staticmethod
    def _own(ctx: IncidentContext, devices: Iterable[str]) -> list[Observation]:
        """Connectivity's own usable checks (latest per device and check) on these devices."""
        wanted = set(devices)
        return [o for metric in OWN_CHECKS for d, o in sorted(latest_checks(ctx, metric).items()) if d in wanted]

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

    def _shared_upstream(self, ctx: IncidentContext, task: Envelope, topo: Topology,
                         reach: dict[str, Observation], faults: list[tuple[Envelope, str]]) -> list[Envelope]:
        """>= 2 distinct fault targets (peer-named or unreachable devices) behind one link that
        is not itself named: that shared link is a genuinely different explanation."""
        down = sorted(d for d, o in reach.items() if as_bool(o) is False and topo.knows(d))
        targets = sorted({t for _, t in faults} | set(down))
        if len(targets) < 2:
            return []
        link = topo.common_upstream(targets)
        if link is None or link in targets:
            return []
        where, head = link, topo.links[link]["b"]
        if head not in targets and head in reach and as_bool(reach[head]) is True:
            where = head          # the device just below the shared link still answers: it is the suspect
        radius = topo.behind(link)
        own = self._own(ctx, radius)
        if not own:
            return []
        consumed = sorted({p.message_id for p, t in faults if t in targets})
        return [self._emit(
            ctx, task, hypothesis=f"upstream_dependency@{where}", stance=Stance.supports,
            evidence_ids=[o.evidence_id for o in own], score=0.7, consumed_message_ids=consumed,
            reason=f"{len(targets)} distinct fault targets ({', '.join(targets)}) all sit behind {link}"
                   + (f"; {head} just below it still answers" if where == head else "")
                   + f"; blast radius {len(radius)} device(s): {', '.join(radius)}; "
                     f"{sum(1 for d in down if d in radius)} unreachable in own checks")]

    def _scope(self, ctx: IncidentContext, task: Envelope, topo: Topology,
               faults: list[tuple[Envelope, str]], notes: list[str]) -> list[Envelope]:
        """For each fault a peer names: its blast radius, judged with Connectivity's own checks only."""
        groups: dict[str, list[Envelope]] = defaultdict(list)
        for peer, _ in faults:
            groups[peer.payload["hypothesis"]].append(peer)
        out: list[Envelope] = []
        for hypothesis in sorted(groups):
            if cause_of(hypothesis) == "weak_wifi":
                continue                                    # judged through its AP uplink instead
            radius = topo.radius(target_of(hypothesis))
            own = self._own(ctx, radius)
            if not own:
                notes.append(f"no own checks in the blast radius of {hypothesis} ({', '.join(radius)}); "
                             "scope not judged")
                continue
            up = sorted({o.device_id for o in own if o.metric == "reachability" and as_bool(o) is True})
            failing = sorted({o.device_id for o in own if as_bool(o) is False})
            senders = ", ".join(sorted({p.sender for p in groups[hypothesis]}))
            state = ("all checked devices answer: a degradation, not an outage" if not failing
                     else f"own checks fail on {', '.join(failing)}")
            out.append(self._emit(
                ctx, task, hypothesis=hypothesis, stance=Stance.supports,
                evidence_ids=[o.evidence_id for o in own], score=0.5,
                consumed_message_ids=sorted(p.message_id for p in groups[hypothesis]),
                reason=f"scope of {hypothesis} (named by {senders}): blast radius {len(radius)} device(s): "
                       f"{', '.join(radius)}; reachable: {', '.join(up) or 'none checked'}; {state}"))
        return out

    def _wifi_uplinks(self, ctx: IncidentContext, task: Envelope, topo: Topology,
                      reach: dict[str, Observation], faults: list[tuple[Envelope, str]],
                      notes: list[str]) -> list[Envelope]:
        """weak_wifi needs a healthy AP uplink: check it with own path evidence, never from RSSI."""
        aps: dict[str, list[Envelope]] = defaultdict(list)
        for peer, target in faults:
            if cause_of(peer.payload["hypothesis"]) == "weak_wifi":
                aps[target].append(peer)
        warn = loss_warn()
        out: list[Envelope] = []
        for ap in sorted(aps):
            link = topo.parent_link.get(ap)
            if link is None:
                continue
            switch = topo.links[link]["a"]
            loss = [o for o in ctx.for_target(link, "packet_loss_pct")
                    if o.value is not None and not UNUSABLE.intersection(o.quality_flags)]
            checks = [reach[d] for d in (switch, ap) if d in reach]
            if warn is None or not (loss or checks):
                notes.append(f"no own evidence on {link} to check the uplink of {ap}")
                continue
            lossy = [o for o in loss if float(o.value) > warn]
            down = [o for o in checks if as_bool(o) is False]
            ids = [o.evidence_id for o in loss + checks]
            consumed = sorted(p.message_id for p in aps[ap])
            if lossy or down:
                problems = ([f"loss above {warn:g}% in {len(lossy)} sample(s)"] if lossy else []) + \
                           ([f"{', '.join(o.device_id or '' for o in down)} unreachable"] if down else [])
                out.append(self._emit(
                    ctx, task, hypothesis=f"upstream_dependency@{link}", stance=Stance.supports,
                    evidence_ids=ids, score=0.6, consumed_message_ids=consumed,
                    reason=f"uplink {link} of {ap} is not healthy: {', '.join(problems)}"))
            else:
                out.append(self._emit(
                    ctx, task, hypothesis=f"healthy@{link}", stance=Stance.contradicts,
                    target_hypothesis=f"upstream_dependency@{link}", evidence_ids=ids, score=0.7,
                    consumed_message_ids=consumed,
                    reason=f"uplink {link} of {ap} is healthy ({len(loss)} loss sample(s) at or below {warn:g}%"
                           + (f", {switch} reachable" if switch in reach else "")
                           + "): the weak Wi-Fi is local to the radio, as its signature requires"))
        return out

    # ----------------------------------------------------------------- path health

    def _path_health(self, ctx: IncidentContext, task: Envelope, topo: Topology, reach: dict[str, Observation],
                     faults: list[tuple[Envelope, str]], already: set[str]) -> list[Envelope]:
        """`healthy@L contradicts upstream_dependency@L` for each link whose every downstream
        device answered. A statement about paths, never about the campus; none at all when
        any observation in the Bundle is missing or stale."""
        if any(INCOMPLETE.intersection(o.quality_flags) for o in ctx.observations):
            return []
        fault_targets = {t for _, t in faults}
        out: list[Envelope] = []
        for link in sorted(topo.links):
            radius = topo.behind(link)
            if f"healthy@{link}" in already or fault_targets & ({link} | set(radius)):
                continue
            checks = [reach.get(d) for d in radius]
            if not radius or any(o is None or as_bool(o) is not True for o in checks):
                continue
            out.append(self._emit(
                ctx, task, hypothesis=f"healthy@{link}", stance=Stance.contradicts,
                target_hypothesis=f"upstream_dependency@{link}",
                evidence_ids=[o.evidence_id for o in checks if o is not None], score=0.6,
                reason=f"every device behind {link} ({', '.join(radius)}) answered its reachability check"))
        return out


def handle_bundle(messages: list[Envelope], observations: list[Observation],
                  topology: dict[str, Any]) -> list[Envelope]:
    """Plug-in contract (docs/PHASE2_TASKS.md D5): messages[0] is the TASK, the rest are peers."""
    return ConnectivityAgent().handle_bundle(messages, observations, topology)
