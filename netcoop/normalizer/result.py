"""Result passed from normalization to the incident pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from netcoop.protocol.models import Observation


@dataclass
class NormalizationResult:
    observations: list[Observation] = field(default_factory=list)
    normalization_log: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    window_end: datetime | None = None
