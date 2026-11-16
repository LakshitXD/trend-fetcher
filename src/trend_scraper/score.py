"""Trend score: how hard a video is outperforming right now, not how big it is.

Raw views favour big channels and old uploads. The score multiplies three signals,
each log-damped so one extreme value cannot dominate:
  - views per day since upload (momentum)
  - views per channel subscriber (breakout beyond the existing audience)
  - likes + comments per view (the audience cared)
Unknown signals count as neutral (1.0) rather than zero.

Paid ads served as Shorts or pre-rolls rack up views without likes. Those are flagged
and down-ranked: they show what a brand paid to push, not what audiences chose.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from .models import TrendScore, Video


def trend_score(v: Video, now: datetime | None = None) -> TrendScore:
    now = now or datetime.now(UTC)
    age_days = max((now - v.published_at).total_seconds() / 86400, 0.5)
    vpd = v.views / age_days
    multiple = v.views / v.channel_subscribers if v.channel_subscribers else None
    engaged = (v.likes or 0) + (v.comments or 0)
    engagement = engaged / v.views if v.views and v.likes is not None else None

    promoted = v.views >= 100_000 and engagement is not None and engagement < 0.001

    score = math.log10(1 + vpd)
    score *= 1 + math.log10(1 + multiple) if multiple is not None else 1.0
    score *= 1 + 10 * engagement if engagement is not None else 1.0
    if promoted:
        score *= 0.25
    return TrendScore(
        views_per_day=vpd,
        subscriber_multiple=multiple,
        engagement_rate=engagement,
        likely_promoted=promoted,
        score=round(score, 3),
    )
