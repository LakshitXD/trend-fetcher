# trend-scraper

A prototype that takes a seller's product and returns what is trending on YouTube for that product's buyers, why it works, and a content brief built on those patterns.

## Pipeline

1. **Queries.** Claude expands the product into search queries per market. For India, they mix English, Hinglish and Hindi.
2. **Discover.** Each query is searched for the last N days, sorted by views. A market is a region (`IN`, `global`) plus a format (`shorts`, `long`), and one run covers every combination.
3. **Score.** Videos are ranked by trend score, which is views per day × views per channel subscriber × engagement, each log-damped. This rewards breakouts over channel size. See `score.py`.
4. **Gate.** Claude drops viral videos that are off-topic for the product.
5. **Watch.** Each selected video is turned into evidence at a depth tier (`depth.py`):
   - `metadata` uses title, description, tags and stats.
   - `transcript` adds timestamped captions. Many Shorts have none, and those escalate to `watch`.
   - `watch` has Gemini view the public video by URL and describe the visuals, on-screen text, audio and edit pace.
6. **Analyse.** Claude reads the evidence and returns a structured `VideoAnalysis` for each video. It covers hook, format, beats, pacing, visual style, product integration, CTA and confidence.
7. **Brief.** Claude finds the recurring patterns and cites the video IDs behind each one. It then proposes 5 content ideas, with a hook and outline for each, plus what to avoid.

## Run

```bash
cp .env.example .env        # add keys
uv run trend-scraper examples/hair-oil.json --regions IN,global --formats shorts,long
uv run trend-scraper examples/hair-oil.json --stop-after discover --no-llm-queries   # no keys needed
uv run trend-scraper examples/hair-oil.json --compare-tiers                          # all tiers on the same videos
```

Each run writes `runs/<time>-<product>/`, which holds these files:

- `candidates.md` and `candidates.json` hold the ranked videos.
- `analyses.json` holds the per-video analyses.
- `tier_comparison.md` compares fetch time, gaps, confidence, and the hook, pacing and visuals each tier recovered.
- `brief.md` and `brief.json` hold the brief.

Searches and evidence are cached in `.cache/`, so re-runs don't spend quota.

## Keys and limits

- **`YOUTUBE_API_KEY`** enables the official Data API v3. Published sources put a search at 100 quota units of a 10,000-unit daily default, and stats lookups at 1 unit per 50 videos. At those rates, a default run (1 region × 2 formats × 4 queries) uses about 800 units. Check the [official quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost) before planning volume, because search quota may have changed in 2026. The tool prints the units each run uses. Without a key, the tool falls back to yt-dlp. That fallback is for the prototype only, because it ignores region and scraping is against YouTube's terms.
- **`GEMINI_API_KEY`** enables the `watch` tier. It works only on public videos, one video per request, and the free tier allows 8 hours of video per day.
- **Claude** is reached through any credential the Anthropic SDK resolves. Calls use structured outputs and server-side refusal fallbacks (`fallbacks: "default"`).

The Shorts detection rule is duration ≤ 180 s. YouTube's API has no Shorts flag, so long vertical clips and short horizontal ones can be misclassified.
