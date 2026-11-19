"""CLI orchestrator that generates public/deals_feed.json for ToolDrop."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parent.parent
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from bot.affiliate_rewriter import rewrite_affiliate_url
from bot.deal_parser import parse_deal
from bot.fcm_notifier import notify_hot_deals
from bot.models import DealsFeedPayload, ParsedDeal, ToolBrand
from bot.rss_sources import DEFAULT_FEED_SOURCES, FeedSource, fetch_all_feeds, fetch_feed_bytes

LOGGER = logging.getLogger(__name__)
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "public" / "deals_feed.json"


def _load_existing_payload(output_path: Path) -> tuple[DealsFeedPayload | None, set[str]]:
    if not output_path.exists():
        return None, set()
    try:
        raw = json.loads(output_path.read_text(encoding="utf-8"))
        deals_raw = raw.get("deals", [])
        if not isinstance(deals_raw, list):
            return None, set()
        deals = [
            ParsedDeal(
                id=str(item["id"]),
                title=str(item["title"]),
                brand=ToolBrand(str(item["brand"])),
                retailer=str(item["retailer"]),
                currentPrice=item.get("currentPrice"),
                originalPrice=item.get("originalPrice"),
                discountPercent=item.get("discountPercent"),
                promoCode=item.get("promoCode"),
                dealUrl=str(item["dealUrl"]),
                imageUrl=item.get("imageUrl"),
                source=str(item["source"]),
                publishedAt=str(item["publishedAt"]),
                isHot=bool(item.get("isHot", False)),
            )
            for item in deals_raw
        ]
        existing_ids = {deal.id for deal in deals}
        payload = DealsFeedPayload(
            schemaVersion=int(raw.get("schemaVersion", 1)),
            generatedAt=str(raw.get("generatedAt", "")),
            dealsCount=len(deals),
            deals=deals,
        )
        return payload, existing_ids
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Could not read existing feed %s: %s", output_path, exc)
        return None, set()


def _deduplicate_and_sort(deals: list[ParsedDeal]) -> list[ParsedDeal]:
    by_id: dict[str, ParsedDeal] = {}
    for deal in deals:
        if deal.id not in by_id:
            by_id[deal.id] = deal
    return sorted(
        by_id.values(),
        key=lambda d: (d.currentPrice is not None, d.isHot, d.publishedAt),
        reverse=True,
    )


def generate_feed(
    output_path: Path = DEFAULT_OUTPUT_PATH,
    sources: tuple[FeedSource, ...] = DEFAULT_FEED_SOURCES,
    fetcher: Callable[[str, int], bytes] = fetch_feed_bytes,
    associate_tag: str | None = None,
) -> DealsFeedPayload:
    """Fetch, parse, deduplicate, affiliate-tag, and persist the deals JSON feed."""
    existing_payload, existing_ids = _load_existing_payload(output_path)

    raw_items = fetch_all_feeds(sources=sources, fetcher=fetcher)
    parsed_deals: list[ParsedDeal] = []
    for raw_item in raw_items:
        deal = parse_deal(raw_item)
        rewritten_url = rewrite_affiliate_url(deal.dealUrl, associate_tag=associate_tag)
        if rewritten_url != deal.dealUrl:
            deal = replace(deal, dealUrl=rewritten_url)
        parsed_deals.append(deal)

    deduped_deals = _deduplicate_and_sort(parsed_deals)
    if not deduped_deals and existing_payload is not None and existing_payload.dealsCount > 0:
        LOGGER.warning("All feeds returned 0 tool deals; preserving existing %s", output_path)
        return existing_payload

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = DealsFeedPayload(
        schemaVersion=1,
        generatedAt=generated_at,
        dealsCount=len(deduped_deals),
        deals=deduped_deals,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    notify_hot_deals(deduped_deals, previous_ids=existing_ids)
    return payload


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for generating public/deals_feed.json."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Generate ToolDrop power tool deals JSON feed.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Destination path for deals_feed.json",
    )
    parser.add_argument(
        "--associate-tag",
        default=None,
        help="Optional Amazon Associates tag override",
    )
    args = parser.parse_args(argv)
    payload = generate_feed(output_path=args.output, associate_tag=args.associate_tag)
    print(f"Generated {payload.dealsCount} deals at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
