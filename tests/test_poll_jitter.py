"""Phase 4 held-out run 1 finding: poller jitter must not read as a missing interval.

Case INC-H3-034 (seed 30034): five utilization samples, all above 90 %, 45-73 s apart. The interval
was taken as the smallest spacing (45 s), so the 73 s spacing (> 1.5 x 45) broke the sustained run.
The interval is now the median spacing; a genuinely missing sample (about 2x) still ends the run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from netcoop.agents.correlation import _sample_interval, sustained_run
from netcoop.protocol.models import Observation

T0 = datetime(2026, 11, 5, 17, 52, 37, tzinfo=timezone.utc)


def series(offsets: list[int], values: list[float]) -> list[Observation]:
    return [Observation(evidence_id=f"EV-{i:03d}", observed_at=T0 + timedelta(seconds=s), link_id="ACC_LINK_03",
                        source_id="switch-telemetry", metric="link_utilization_pct", value=v, unit="percent",
                        quality_flags=["ok"], original_field="util") for i, (s, v) in enumerate(zip(offsets, values), 1)]


JITTERED = [0, 45, 118, 175, 243]            # INC-H3-034's real spacing: 45, 73, 57, 68 s
UTIL = [97.4, 91.1, 97.0, 91.3, 92.2]


def test_interval_is_the_median_spacing():
    assert _sample_interval(series(JITTERED, UTIL)) == 62.5


def test_jittered_poll_still_forms_a_sustained_run():
    assert len(sustained_run(series(JITTERED, UTIL), 90, 80, 3)) == 3


def test_a_really_missing_sample_still_ends_the_run():
    # 60 s poll with the third sample missing: 60, 120, 60 s -> run of 2, gap, run of 2
    assert sustained_run(series([0, 60, 180, 240], [95, 96, 97, 98]), 90, 80, 3) == []
