"""What the normalizer hands to the orchestrator. The pipeline depends only on this
shape, so normalize.py can evolve without touching orchestrator code."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from netcoop.protocol.models import Observation


@dataclass
class NormalizationResult:
    observations: list[Observation] = field(default_factory=list)
    normalization_log: list[str] = field(default_factory=list)   # e.g. "rtt_sec 0.28 -> latency_ms 280"
    validation_errors: list[str] = field(default_factory=list)   # non-empty => invalid_input report
    window_end: Optional[datetime] = None                        # incident clock; timezone-aware
    on_demand: list[Observation] = field(default_factory=list)   # probe samples released only to answer a QUERY
    skipped: list[str] = field(default_factory=list)   # typed, e.g. "INVALID_VALUE records[3].metrics.x: ...; skipped"
    suspect: list[str] = field(default_factory=list)   # kept but doubtful, e.g. utilization that looks like a 0-1 fraction
