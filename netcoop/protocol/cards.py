"""Agent cards: what each agent advertises. The orchestrator routes by these and
flags canonical metrics no card owns."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from netcoop.protocol.models import SCHEMA_VERSION


class AgentCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    endpoint: str = Field(description="'local' for in-process agents, else an http(s) URL")
    protocol_versions: list[str]
    capabilities: list[str]
    owned_metrics: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list, description="Causes this agent may assert")


V = [f"netcoop/{SCHEMA_VERSION}"]

# owned_metrics match data/field_mapping.md; tests/test_signatures.py and tests/test_examples.py check them.
CARDS: dict[str, AgentCard] = {
    c.agent_id: c
    for c in [
        AgentCard(
            agent_id="performance_agent",
            endpoint="local",
            protocol_versions=V,
            capabilities=["analyze_performance"],
            owned_metrics=["latency_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps",
                           "capacity_mbps", "link_utilization_pct"],
            hypotheses=["link_congestion", "healthy"],
        ),
        AgentCard(
            agent_id="infrastructure_agent",
            endpoint="local",
            protocol_versions=V,
            capabilities=["analyze_infrastructure"],
            owned_metrics=["cpu_pct", "memory_pct", "queue_drops", "interface_errors",
                           "link_flap_count", "interface_oper_status", "rssi_dbm", "retry_rate_pct"],
            hypotheses=["interface_fault", "device_overload", "weak_wifi", "hardware_failure",
                        "link_congestion", "healthy"],
        ),
        AgentCard(
            agent_id="connectivity_agent",
            endpoint="http://127.0.0.1:8001",
            protocol_versions=V,
            capabilities=["analyze_connectivity", "interpret_peer_findings"],
            owned_metrics=["dns_success", "dhcp_success", "reachability"],
            hypotheses=["dns_failure", "dhcp_failure", "upstream_dependency", "healthy"],
        ),
        AgentCard(
            agent_id="correlation_agent",
            endpoint="local",
            protocol_versions=V,
            capabilities=["correlate", "query"],
        ),
        AgentCard(
            agent_id="reporting_agent",
            endpoint="local",
            protocol_versions=V,
            capabilities=["report"],
        ),
    ]
}
