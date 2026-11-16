"""trend-scraper: product in, trending YouTube patterns and a content brief out.

  uv run trend-scraper examples/hair-oil.json --regions IN,global --formats shorts,long
  uv run trend-scraper examples/hair-oil.json --stop-after discover          # no LLM keys needed
  uv run trend-scraper examples/hair-oil.json --compare-tiers                # all tiers on the same videos
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from . import depth, llm
from .models import Candidate, Evidence, Format, Market, Product, Video, VideoAnalysis
from .score import trend_score
from .sources import Source, YouTubeAPI, YtDlp

CACHE = Path(".cache")
LANG = {"IN": "hi"}


def cached_search(src: Source, query: str, m: Market, days: int, limit: int) -> list[Video]:
    key = hashlib.sha1(f"{src.name}|{query}|{m.label}|{days}|{limit}|{datetime.now():%Y-%m-%d}".encode()).hexdigest()
    path = CACHE / "search" / f"{key}.json"
    if path.exists():
        return [Video.model_validate(v) for v in json.loads(path.read_text())]
    videos = src.search(query, m, days, limit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([v.model_dump(mode="json") for v in videos]))
    return videos


def cached_evidence(tier: str, v: Video) -> Evidence:
    path = CACHE / "evidence" / tier / f"{v.id}.json"
    if path.exists():
        return Evidence.model_validate_json(path.read_text())
    ev = depth.TIERS[tier](v)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ev.model_dump_json())
    return ev


def discover(src: Source, product: Product, m: Market, args) -> list[Candidate]:
    queries = product.keywords[: args.queries] if args.no_llm_queries else llm.expand_queries(product, m, args.queries)
    seen: dict[str, Candidate] = {}
    for q in queries:
        print(f"  [{m.label}] searching: {q}")
        for v in cached_search(src, q, m, args.days, args.per_query):
            seen.setdefault(v.id, Candidate(video=v, market=m.label, query=q, trend=trend_score(v)))
    return sorted(seen.values(), key=lambda c: c.trend.score, reverse=True)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def table(cands: list[Candidate]) -> str:
    rows = ["| # | market | video | views | views/day | x subs | engagement | score | relevant |", "|---|---|---|---|---|---|---|---|---|"]
    for i, c in enumerate(cands, 1):
        t, v = c.trend, c.video
        rows.append(
            f"| {i} | {c.market} | [{v.title[:60]}]({v.url}) | {v.views:,} | {t.views_per_day:,.0f} | "
            f"{f'{t.subscriber_multiple:.1f}' if t.subscriber_multiple is not None else '?'} | "
            f"{f'{t.engagement_rate:.1%}' if t.engagement_rate is not None else '?'} | {t.score} | "
            f"{'?' if c.relevant is None else 'yes' if c.relevant else 'no: ' + c.relevance_reason}"
            f"{' (likely ad)' if t.likely_promoted else ''} |"
        )
    return "\n".join(rows)


def tier_comparison(rows: list[tuple[Candidate, Evidence, VideoAnalysis]]) -> str:
    out = ["| video | tier | fetch s | missing | confidence | hook | pacing | visual style |", "|---|---|---|---|---|---|---|---|"]
    for c, ev, a in rows:
        cell = lambda s: s.replace("|", "/").replace("\n", " ")[:120]
        out.append(
            f"| {c.video.id} | {ev.tier} | {ev.seconds:.1f} | {', '.join(ev.missing) or '-'} | {a.confidence} | "
            f"{cell(a.hook)} | {cell(a.pacing)} | {cell(a.visual_style)} |"
        )
    return "\n".join(out)


def brief_md(product: Product, brief, cands: list[Candidate]) -> str:
    lines = [f"# Content brief: {product.name}", "", brief.summary, "", "## Patterns"]
    for p in brief.patterns:
        lines += [f"### {p.name}", p.description, f"Seen in: {', '.join(p.evidence_video_ids)}", f"Fit: {p.fit_for_product}", ""]
    lines.append("## Ideas")
    for i, idea in enumerate(brief.ideas, 1):
        lines += [f"### {i}. {idea.title} ({idea.format})", f"Hook: {idea.hook}"]
        lines += [f"{n}. {b}" for n, b in enumerate(idea.outline, 1)]
        lines += [f"Built on: {', '.join(idea.based_on_patterns)}", f"Why: {idea.why_it_fits}", ""]
    lines += ["## Avoid", *[f"- {a}" for a in brief.avoid], "", "## Source videos", table(cands)]
    return "\n".join(lines)


def dry_run_report(out: Path, product: Product, by_market: dict[str, list[Candidate]], picked: list[Candidate], args) -> None:
    """Fetches the real evidence the AI steps would read and writes down every call they would make."""
    rows, rank_in_market, watch, analyses = [], {}, [], 0
    for c in picked:
        rank = rank_in_market[c.market] = rank_in_market.get(c.market, 0) + 1
        ev = cached_evidence("transcript", c.video)
        no_captions = "transcript" in ev.missing
        to_watch = rank <= args.watch_top or no_captions
        watch += [c.video.id] if to_watch else []
        analyses += 1 + to_watch
        lang = re.search(r"Transcript \((\S+), (\S+)\)", ev.text)
        captions = f"{lang[1]} ({lang[2]})" if lang else "none"
        reason = "no captions" if no_captions else "top of market" if to_watch else ""
        rows.append(
            f"| {c.market} | [{c.video.title[:55]}]({c.video.url}) | {c.video.format} {c.video.duration_s}s | "
            f"{c.trend.score} | {captions} | {len(ev.text):,} | {'yes: ' + reason if to_watch else 'no'} |"
        )
    cands = sum(min(len(v), args.analyse * 3) for v in by_market.values() if v)
    md = [
        f"# Dry run: {product.name}",
        "",
        "Real YouTube discovery and transcripts. No Claude or Gemini calls were made.",
        "",
        "## Selected for analysis",
        "Picked by trend score, skipping likely ads. In a real run, Claude's relevance check picks these instead.",
        "",
        "| market | video | format | score | captions | evidence chars | Gemini watch |",
        "|---|---|---|---|---|---|---|",
        *rows,
        "",
        "## AI calls a real run would make",
        f"- Claude, query expansion: {len(by_market)} calls (one per market). This run used the product's own keywords.",
        f"- Claude, relevance check: {sum(1 for v in by_market.values() if v)} calls covering {cands} candidates",
        f"- Gemini, watch: {len(watch)} videos ({', '.join(watch) or 'none'})",
        f"- Claude, per-video analysis: {analyses} calls",
        "- Claude, brief: 1 call",
        "",
        "## Candidates",
        table([c for cs in by_market.values() for c in cs]),
    ]
    (out / "dry_run.md").write_text("\n".join(md))
    for c in picked:
        (out / "evidence").mkdir(exist_ok=True)
        (out / "evidence" / f"{c.video.id}.txt").write_text(cached_evidence("transcript", c.video).text)
    print("\n".join(md[: md.index("## Candidates")]))
    print(f"Report: {out / 'dry_run.md'}  Evidence: {out / 'evidence'}/")


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("product", type=Path, help="product JSON file")
    ap.add_argument("--regions", default="IN", help="comma list of region codes; 'global' for none")
    ap.add_argument("--formats", default="shorts,long")
    ap.add_argument("--days", type=int, default=30, help="only videos published in the last N days")
    ap.add_argument("--queries", type=int, default=4, help="search queries per market")
    ap.add_argument("--per-query", type=int, default=10)
    ap.add_argument("--analyse", type=int, default=4, help="top relevant videos to analyse per market")
    ap.add_argument("--tier", choices=list(depth.TIERS), default="transcript", help="depth for analysed videos")
    ap.add_argument("--watch-top", type=int, default=1, help="also watch the top N per market with Gemini (needs GEMINI_API_KEY)")
    ap.add_argument("--compare-tiers", action="store_true", help="run every tier on every analysed video and write a comparison")
    ap.add_argument("--no-llm-queries", action="store_true", help="search the product's own keywords instead of Claude-expanded queries")
    ap.add_argument("--source", choices=["auto", "api", "yt-dlp"], default="auto")
    ap.add_argument("--stop-after", choices=["discover", "gate", "analyse"], default=None)
    ap.add_argument("--dry-run", action="store_true", help="real YouTube data, no Claude or Gemini calls: shows what each AI step would get")
    args = ap.parse_args()
    if args.dry_run:
        args.no_llm_queries = True

    product = Product.model_validate_json(args.product.read_text())
    key = os.environ.get("YOUTUBE_API_KEY")
    if args.source == "api" or (args.source == "auto" and key):
        src: Source = YouTubeAPI(key)
    else:
        print("Using yt-dlp (prototype only: no region filter, not for production).")
        src = YtDlp()
    markets = [
        Market(region=None if r == "global" else r, language=LANG.get(r), format=Format(f))
        for r in args.regions.split(",")
        for f in args.formats.split(",")
    ]
    out = Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S}-{slug(product.name)}"
    out.mkdir(parents=True)
    print(f"Run folder: {out}")

    by_market = {m.label: discover(src, product, m, args) for m in markets}
    all_cands = [c for cs in by_market.values() for c in cs]
    (out / "candidates.json").write_text(json.dumps([c.model_dump(mode="json") for c in all_cands], indent=1))
    if isinstance(src, YouTubeAPI):
        print(f"YouTube quota used: {src.quota_used} units")
    if args.stop_after == "discover":
        (out / "candidates.md").write_text(table(all_cands))
        print(table(all_cands))
        return

    picked: list[Candidate] = []
    taken: set[str] = set()
    for label, cands in by_market.items():
        pool = [c for c in cands if c.video.id not in taken][: args.analyse * 3]
        if not pool:
            continue
        if args.dry_run:
            chosen = [c for c in pool if not c.trend.likely_promoted][: args.analyse]
        else:
            llm.gate_relevance(product, pool)
            chosen = [c for c in pool if c.relevant][: args.analyse]
        picked += chosen
        taken |= {c.video.id for c in chosen}
    if args.dry_run:
        dry_run_report(out, product, by_market, picked, args)
        return
    (out / "candidates.md").write_text(table(all_cands))
    print(table(picked))
    if args.stop_after == "gate":
        return

    gemini = bool(os.environ.get("GEMINI_API_KEY"))
    rows: list[tuple[Candidate, Evidence, VideoAnalysis]] = []
    best: dict[str, VideoAnalysis] = {}
    rank_in_market: dict[str, int] = {}
    for c in picked:
        rank = rank_in_market[c.market] = rank_in_market.get(c.market, 0) + 1
        tiers = list(depth.TIERS) if args.compare_tiers else [args.tier]
        if args.tier == "transcript" and not args.compare_tiers:
            no_captions = "transcript" in cached_evidence("transcript", c.video).missing
            if rank <= args.watch_top or no_captions:
                tiers.append("watch")
        if "watch" in tiers and not gemini:
            tiers.remove("watch")
            print(f"  skipping watch tier for {c.video.id}: GEMINI_API_KEY not set")
        for tier in tiers:
            print(f"  analysing {c.video.id} at tier {tier}")
            ev = cached_evidence(tier, c.video)
            a = llm.analyse(ev, c.video.id)
            rows.append((c, ev, a))
            best[c.video.id] = a  # TIERS order is shallow to deep, so the deepest run wins
    (out / "analyses.json").write_text(json.dumps([a.model_dump() for _, _, a in rows], indent=1))
    if args.compare_tiers or any(ev.tier == "watch" for _, ev, _ in rows):
        (out / "tier_comparison.md").write_text(tier_comparison(rows))
    if args.stop_after == "analyse":
        return

    brief = llm.synthesise(product, list(best.values()), picked)
    (out / "brief.json").write_text(brief.model_dump_json(indent=1))
    (out / "brief.md").write_text(brief_md(product, brief, picked))
    print(f"\nBrief written to {out / 'brief.md'}")
