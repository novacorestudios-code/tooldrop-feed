"""Defensive RSS/Atom fetching and tool-deal filtering for ToolDrop."""

from __future__ import annotations

import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable

from bot.deal_parser import strip_html
from bot.models import RawFeedItem

LOGGER = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "ToolDropBot/1.0 (+https://github.com/tooldrop)"
DEFAULT_TIMEOUT_SECONDS = 10

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"
_MEDIA_NS = "{http://search.yahoo.com/mrss/}"
_DEALNEWS_NS = "{https://www.dealnews.com/ns/rss/1.0.htm}"


@dataclass(frozen=True)
class FeedSource:
    """Configuration for a single RSS/Atom deal feed."""

    name: str
    url: str


DEFAULT_FEED_SOURCES: tuple[FeedSource, ...] = (
    FeedSource(
        name="Slickdeals",
        url=(
            "https://slickdeals.net/newsearch.php?searcharea=deals&searchin=first"
            "&rss=1&q=dewalt+OR+milwaukee+OR+makita"
        ),
    ),
    FeedSource(
        name="Slickdeals",
        url=(
            "https://slickdeals.net/newsearch.php?searcharea=deals&searchin=first"
            "&rss=1&q=ryobi+OR+bosch+OR+craftsman"
        ),
    ),
    FeedSource(
        name="ToolGuyd",
        url="https://toolguyd.com/feed/",
    ),
    FeedSource(
        name="DealNews",
        url="https://www.dealnews.com/c196/Home-Garden/Tools-Hardware/?rss=1",
    ),
)

_EXCLUDED_CATEGORIES = frozenset(
    {
        "small appliances",
        "vacuum cleaners",
        "babies & kids items",
        "air purifiers",
        "security cameras",
        "home security",
        "kitchen",
        "appliances",
        "coffee",
        "laundry supplies",
        "groceries",
        "decor",
        "outdoor lighting",
        "3d printers",
        "household items",
        "personal care",
        "beauty",
        "toys",
    }
)

_EXCLUDED_KEYWORDS_RE = re.compile(
    r"\b(?:"
    r"robot\s+vacuum|roborock|roomba|dyson|vacuum\s+mop|vacuum\s+cleaner|vacuum\s+storage\s+bag|"
    r"air\s+purifier|security\s+camera|indoor\s+cam|ring\s+security|doorbell\s+cam|"
    r"smart\s+bulb|light\s+bulb|wall\s+light|outdoor\s+lights|smart\s+wifi\s+.*plug|"
    r"lego|furby|television|\btv\b|sofa|couch|throw\s+blanket|blanket|comforter|curtains|"
    r"blender|stand\s+mixer|espresso|french\s+press|coffee\s+roasters|high\s+chair|"
    r"massage\s+gun|theragun|ethernet\s+adapter|streaming\s+stick|roku|usb-c\s+.*car\s+charger|"
    r"coin\s+batteries|cr2032|avocado\s+oil|dog\s+food|peanuts|cookies|eau\s+de\s+parfum|"
    r"maxi\s+skirt|maxi\s+pads|\bbra\b|bug\s+zapper|privacy\s+trees|arborvitae|"
    r"tumbler|quencher|water\s+bottle|thermos|\bmug\b|flask|cooler\s+bag|lunch\s+box|"
    r"costco\s+1-year\s+.*membership|snack\s+sale"
    r")\b",
    re.IGNORECASE,
)

_TOOL_BRAND_OR_CATEGORY_RE = re.compile(
    r"\b(?:"
    r"de[\s-]?walt|flexvolt|toughsystem|tstak|milwaukee|m18|m12|packout|redlithium|mx\s*fuel|"
    r"makita|lxt|xgt|ryobi|bosch|craftsman|ridgid|harbor\s*freight|bauer|hercules|"
    r"kobalt|fluke|knipex|klein\s+tools?|metabo|skil|greenworks|ego\s+power|gearwrench|"
    r"irwin|wera|wiha|channellock|stanley|porter-cable|dremel|festool|sawstop|wen|"
    r"tekton|sunex|neiko|johnson\s+level|kaiweets|husky|pittsburgh|quinn|doyle|"
    r"u\.?s\.?\s*general|central\s*machinery"
    r")\b",
    re.IGNORECASE,
)

_TOOL_HARDWARE_KEYWORDS_RE = re.compile(
    r"\b(?:"
    r"power\s+tool|hand\s+tool|cordless\s+tool|tools?\s+&\s+hardware|mechanics\s+tool|"
    r"hammer\s+drill|impact\s+driver|impact\s+wrench|drill\s+kit|combo\s+kit|bare\s+tool|"
    r"circular\s+saw|miter\s+saw|table\s+saw|track\s+saw|reciprocating\s+saw|jigsaw|chainsaw|"
    r"angle\s+grinder|rotary\s+hammer|oscillating\s+.*tool|sander|router|planer|nailer|"
    r"air\s+compressor|inverter\s+generator|generator|pressure\s+washer|welder|"
    r"multimeter|clamp\s+meter|laser\s+level|sight\s+level|tape\s+measure|"
    r"torque\s+wrench|socket\s+set|ratchet|pliers|wire\s+stripping|wire\s+stripper|"
    r"nut\s+driver|hole\s+saw|drill\s+bit|saw\s+blade|heat\s+shrink|"
    r"tool\s*box|toolbox|tool\s+chest|tool\s+bag|work\s+light|headlamp|work\s+gloves|"
    r"work\s+platform|construction\s+jack|shop\s+vac(?:uum)?|outdoor\s+power\s+tool|"
    r"(?:20v|18v|12v|40v|60v|flexvolt|lithium-ion)\s+.*batter(?:y|ies)|"
    r"starter\s+kit\s+.*batter"
    r")\b",
    re.IGNORECASE,
)

_TOOL_DEAL_INDICATOR_RE = re.compile(
    r"(?:\$|\b(?:deal|deals|sale|save|clearance|black\s+friday|lower\s+pricing|bogo|hack)\b|\b\d{1,2}\s*%\s*off\b|\$\d[\d,]*(?:\.\d{1,2})?\s*off\b)",
    re.IGNORECASE,
)

_TOOL_CATEGORIES = frozenset(
    {
        "tool deals",
        "tools & hardware",
        "hand tools",
        "power tools",
        "measuring devices",
        "levels",
        "outdoor power equipment",
        "tool storage",
        "electrical tools",
        "cordless",
        "drills & drivers",
        "saws",
    }
)


def fetch_feed_bytes(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    """Fetch raw XML bytes from an RSS/Atom URL with timeout and User-Agent."""
    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _parse_float(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw.strip().replace(",", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def _extract_rss_item(element: ET.Element, source_name: str) -> RawFeedItem | None:
    title = (element.findtext("title") or "").strip()
    link = (element.findtext("link") or "").strip()
    if not title or not link:
        return None

    description = element.findtext("description") or ""
    content = element.findtext(f"{_CONTENT_NS}encoded") or ""
    pub_date = (
        element.findtext("pubDate")
        or element.findtext(f"{_DC_NS}date")
        or ""
    ).strip()

    image_url: str | None = None
    for tag in (f"{_MEDIA_NS}content", f"{_MEDIA_NS}thumbnail", "enclosure"):
        node = element.find(tag)
        if node is not None:
            candidate = (node.attrib.get("url") or "").strip()
            media_type = (node.attrib.get("type") or "").lower()
            medium = (node.attrib.get("medium") or "").lower()
            if candidate and (
                tag != "enclosure" or media_type.startswith("image/") or medium == "image"
            ):
                image_url = candidate
                break

    retailer_hint = (element.findtext(f"{_DEALNEWS_NS}retailer") or "").strip() or None
    price_hint = _parse_float(element.findtext(f"{_DEALNEWS_NS}price"))

    categories: list[str] = []
    for cat_tag in ("category", f"{_DEALNEWS_NS}category"):
        for cat in element.findall(cat_tag):
            if cat.text and cat.text.strip():
                categories.append(cat.text.strip())

    return RawFeedItem(
        title=title,
        link=link,
        description=description,
        content=content,
        pub_date=pub_date,
        source=source_name,
        image_url=image_url,
        retailer_hint=retailer_hint,
        price_hint=price_hint,
        categories=tuple(categories),
    )


def _extract_atom_entry(element: ET.Element, source_name: str) -> RawFeedItem | None:
    title = (element.findtext(f"{_ATOM_NS}title") or "").strip()
    link = ""
    for link_el in element.findall(f"{_ATOM_NS}link"):
        rel = link_el.attrib.get("rel", "alternate")
        href = (link_el.attrib.get("href") or "").strip()
        if href and rel in ("alternate", ""):
            link = href
            break
    if not title or not link:
        return None

    summary = element.findtext(f"{_ATOM_NS}summary") or ""
    content = element.findtext(f"{_ATOM_NS}content") or ""
    pub_date = (
        element.findtext(f"{_ATOM_NS}published")
        or element.findtext(f"{_ATOM_NS}updated")
        or ""
    ).strip()

    categories = tuple(
        (c.attrib.get("term") or c.text or "").strip()
        for c in element.findall(f"{_ATOM_NS}category")
        if (c.attrib.get("term") or c.text or "").strip()
    )

    return RawFeedItem(
        title=title,
        link=link,
        description=summary,
        content=content,
        pub_date=pub_date,
        source=source_name,
        categories=categories,
    )


def parse_feed_xml(xml_bytes: bytes, source_name: str) -> list[RawFeedItem]:
    """Parse RSS 2.0 or Atom XML bytes into RawFeedItem objects."""
    if not xml_bytes.strip():
        return []
    root = ET.fromstring(xml_bytes)
    items: list[RawFeedItem] = []
    for item_el in root.findall(".//item"):
        parsed = _extract_rss_item(item_el, source_name)
        if parsed is not None:
            items.append(parsed)
    for entry_el in root.findall(f".//{_ATOM_NS}entry"):
        parsed = _extract_atom_entry(entry_el, source_name)
        if parsed is not None:
            items.append(parsed)
    return items


def is_tool_deal(item: RawFeedItem) -> bool:
    """Return True only for genuine power/hand tool, battery, storage, or workshop deals."""
    clean_title = strip_html(item.title)
    clean_desc = strip_html(item.description)
    lowered_cats = {cat.strip().lower() for cat in item.categories}

    if lowered_cats & _EXCLUDED_CATEGORIES:
        return False

    if _EXCLUDED_KEYWORDS_RE.search(clean_title):
        return False

    if item.source.lower() == "toolguyd":
        has_deal_cat = "tool deals" in lowered_cats
        if not has_deal_cat and not _TOOL_DEAL_INDICATOR_RE.search(clean_title):
            return False

    if lowered_cats & _TOOL_CATEGORIES:
        return True

    combined = f"{clean_title} {clean_desc}"
    if _TOOL_BRAND_OR_CATEGORY_RE.search(clean_title):
        return True
    if _TOOL_HARDWARE_KEYWORDS_RE.search(combined):
        return True
    return False


def fetch_all_feeds(
    sources: tuple[FeedSource, ...] = DEFAULT_FEED_SOURCES,
    fetcher: Callable[[str, int], bytes] = fetch_feed_bytes,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[RawFeedItem]:
    """Fetch and filter all configured RSS/Atom sources with per-source isolation."""
    collected: list[RawFeedItem] = []
    for source in sources:
        try:
            raw_xml = fetcher(source.url, timeout)
            parsed_items = parse_feed_xml(raw_xml, source_name=source.name)
            tool_items = [item for item in parsed_items if is_tool_deal(item)]
            collected.extend(tool_items)
        except Exception as exc:  # noqa: BLE001 - isolate individual feed failures
            LOGGER.warning("Failed to fetch/parse %s (%s): %s", source.name, source.url, exc)
    return collected
