"""Integrity checks for scenarios/topology.json."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

TOPOLOGY_PATH = Path(__file__).resolve().parents[1] / "scenarios" / "topology.json"
NODE_TYPES = {"router", "distribution_switch", "access_switch", "access_point", "server"}


def load_topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


def reachable_from(topo: dict, start: str, skip_link: str | None = None) -> set[str]:
    adjacency: dict[str, list[str]] = {node["id"]: [] for node in topo["nodes"]}
    for link in topo["links"]:
        if link["link_id"] == skip_link:
            continue
        adjacency[link["a"]].append(link["b"])
        adjacency[link["b"]].append(link["a"])
    seen = {start}
    queue = deque([start])
    while queue:
        for neighbour in adjacency[queue.popleft()]:
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return seen


def test_nodes_and_links_have_required_fields():
    topo = load_topology()
    for node in topo["nodes"]:
        assert {"id", "type", "zone"} <= node.keys()
        assert node["type"] in NODE_TYPES
    for link in topo["links"]:
        assert {"link_id", "a", "b", "capacity_mbps"} <= link.keys()
        assert isinstance(link["capacity_mbps"], (int, float)) and link["capacity_mbps"] > 0


def test_ids_are_unique_across_nodes_and_links():
    topo = load_topology()
    ids = [n["id"] for n in topo["nodes"]] + [l["link_id"] for l in topo["links"]]
    assert len(ids) == len(set(ids))


def test_links_reference_known_nodes():
    topo = load_topology()
    node_ids = {n["id"] for n in topo["nodes"]}
    for link in topo["links"]:
        assert link["a"] in node_ids and link["b"] in node_ids
        assert link["a"] != link["b"]


def test_device_count_and_required_roles():
    topo = load_topology()
    nodes = topo["nodes"]
    assert 10 <= len(nodes) <= 14
    types = [n["type"] for n in nodes]
    assert types.count("router") >= 1
    assert types.count("access_point") >= 2
    assert types.count("access_switch") >= 1
    dist_zones = {n["zone"] for n in nodes if n["type"] == "distribution_switch"}
    assert {"hostel", "department"} <= dist_zones
    servers = [n for n in nodes if n["type"] == "server"]
    assert any({"dns", "dhcp"} <= set(s.get("services", [])) for s in servers)


def test_network_is_fully_connected():
    topo = load_topology()
    all_ids = {n["id"] for n in topo["nodes"]}
    assert reachable_from(topo, "CORE_RTR_01") == all_ids


def test_hostel_uplink_is_shared_upstream():
    """Cutting HOSTEL_UPLINK_01 isolates every hostel device and nothing else."""
    topo = load_topology()
    still_reachable = reachable_from(topo, "CORE_RTR_01", skip_link="HOSTEL_UPLINK_01")
    hostel = {n["id"] for n in topo["nodes"] if n["zone"] == "hostel"}
    others = {n["id"] for n in topo["nodes"]} - hostel
    assert hostel.isdisjoint(still_reachable)
    assert others <= still_reachable