"""Discovery sources. Both return fully populated `Video`s for a query in a market.

`YouTubeAPI` is the official Data API v3 and is what the platform should use.
`YtDlp` needs no key and exists only so the prototype runs before a key is set up;
it cannot filter by region and scraping YouTube is against its terms for production use.
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime, timedelta
from typing import Protocol

from urllib.parse import quote_plus

import httpx

from .models import Format, Market, Video

API = "https://www.googleapis.com/youtube/v3"


class Source(Protocol):
    name: str

    def search(self, query: str, market: Market, days: int, limit: int) -> list[Video]: ...


def parse_iso_duration(value: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _int(value) -> int | None:
    return int(value) if value is not None else None


class YouTubeAPI:
    """Quota: search.list costs 100 units, videos.list and channels.list cost 1 per 50 IDs."""

    name = "youtube_api"

    def __init__(self, api_key: str):
        self.key = api_key
        self.http = httpx.Client(timeout=30)
        self.quota_used = 0

    def _get(self, path: str, cost: int, **params) -> dict:
        r = self.http.get(f"{API}/{path}", params={"key": self.key, **params})
        r.raise_for_status()
        self.quota_used += cost
        return r.json()

    def search(self, query: str, market: Market, days: int, limit: int) -> list[Video]:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": "viewCount",
            "maxResults": min(limit, 50),
            "publishedAfter": (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if market.region:
            params["regionCode"] = market.region
        if market.language:
            params["relevanceLanguage"] = market.language
        # API buckets: short < 4 min, medium 4-20 min. View-sorted results are dominated by Shorts,
        # so long-form must ask for medium or it gets filtered down to almost nothing.
        params["videoDuration"] = "short" if market.format is Format.SHORTS else "medium"
        ids = [item["id"]["videoId"] for item in self._get("search", 100, **params).get("items", [])]
        videos = self.videos(ids)
        return [v for v in videos if v.format is market.format]

    def videos(self, ids: list[str]) -> list[Video]:
        items = []
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            if chunk:
                items += self._get("videos", 1, part="snippet,statistics,contentDetails", id=",".join(chunk))["items"]
        subs = self.subscribers({it["snippet"]["channelId"] for it in items})
        out = []
        for it in items:
            sn, st = it["snippet"], it.get("statistics", {})
            out.append(
                Video(
                    id=it["id"],
                    title=sn["title"],
                    description=sn.get("description", ""),
                    channel_id=sn["channelId"],
                    channel_title=sn.get("channelTitle", ""),
                    channel_subscribers=subs.get(sn["channelId"]),
                    published_at=sn["publishedAt"],
                    duration_s=parse_iso_duration(it["contentDetails"]["duration"]),
                    views=int(st.get("viewCount", 0)),
                    likes=_int(st.get("likeCount")),
                    comments=_int(st.get("commentCount")),
                    tags=sn.get("tags", []),
                    thumbnail_url=sn.get("thumbnails", {}).get("high", {}).get("url", ""),
                    source=self.name,
                )
            )
        return out

    def subscribers(self, channel_ids: set[str]) -> dict[str, int | None]:
        ids, out = sorted(channel_ids), {}
        for i in range(0, len(ids), 50):
            for ch in self._get("channels", 1, part="statistics", id=",".join(ids[i : i + 50]))["items"]:
                st = ch.get("statistics", {})
                out[ch["id"]] = None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount"))
        return out


class YtDlp:
    name = "yt_dlp"

    def __init__(self):
        import yt_dlp

        self.ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True})
        self.flat = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": 60})

    @staticmethod
    def _filter(days: int, shorts: bool) -> str:
        """YouTube's `sp` search filter: sort by views, upload window, videos only, under 4 min for Shorts."""
        window = 3 if days <= 7 else 4 if days <= 31 else 5
        filters = bytes([0x08, window, 0x10, 0x01]) + (bytes([0x18, 0x01]) if shorts else b"")
        return base64.b64encode(bytes([0x08, 0x03, 0x12, len(filters)]) + filters).decode()

    def search(self, query: str, market: Market, days: int, limit: int) -> list[Video]:
        shorts = market.format is Format.SHORTS
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={quote_plus(self._filter(days, shorts))}"
        entries = self.flat.extract_info(url, download=False).get("entries") or []
        entries = [e for e in entries if e.get("id") and e.get("duration")]
        entries = [e for e in entries if (e["duration"] <= 180) == shorts]
        cutoff = datetime.now(UTC) - timedelta(days=days)
        out = []
        for e in entries[: limit * 2]:
            info = self.ydl.extract_info(f"https://www.youtube.com/watch?v={e['id']}", download=False)
            published = datetime.fromtimestamp(info["timestamp"], UTC) if info.get("timestamp") else None
            if published is None or published < cutoff:
                continue
            out.append(
                Video(
                    id=info["id"],
                    title=info.get("title", ""),
                    description=info.get("description") or "",
                    channel_id=info.get("channel_id", ""),
                    channel_title=info.get("channel", ""),
                    channel_subscribers=info.get("channel_follower_count"),
                    published_at=published,
                    duration_s=int(info.get("duration") or 0),
                    views=int(info.get("view_count") or 0),
                    likes=info.get("like_count"),
                    comments=info.get("comment_count"),
                    tags=info.get("tags") or [],
                    thumbnail_url=info.get("thumbnail", ""),
                    source=self.name,
                )
            )
            if len(out) >= limit:
                break
        return out
