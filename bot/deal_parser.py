"""Parsing, normalization, and deduplication logic for ToolDrop deals."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bot.models import ParsedDeal, RawFeedItem, ToolBrand

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_IMG_SRC_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)

_BRAND_PATTERNS: tuple[tuple[ToolBrand, re.Pattern[str]], ...] = (
    (ToolBrand.DEWALT, re.compile(r"\b(?:de[\s-]?walt|flexvolt|toughsystem|tstak)\b", re.IGNORECASE)),
    (ToolBrand.MILWAUKEE, re.compile(r"\b(?:milwaukee|m18|m12|packout|redlithium|mx\s*fuel)\b", re.IGNORECASE)),
    (ToolBrand.MAKITA, re.compile(r"\b(?:makita|lxt|xgt)\b", re.IGNORECASE)),
    (ToolBrand.RYOBI, re.compile(r"\bryobi\b", re.IGNORECASE)),
    (ToolBrand.BOSCH, re.compile(r"\bbosch\b", re.IGNORECASE)),
    (ToolBrand.CRAFTSMAN, re.compile(r"\bcraftsman\b", re.IGNORECASE)),
    (ToolBrand.RIDGID, re.compile(r"\bridgid\b", re.IGNORECASE)),
    (
        ToolBrand.HARBOR_FREIGHT,
        re.compile(
            r"\b(?:harbor\s*freight|bauer|hercules|pittsburgh|quinn|doyle|"
            r"central\s*machinery|u\.?s\.?\s*general|yukon|"
            r"daytona\s+(?:jack|floor\s+jack)|icon\s+(?:tool|torque|wrench|socket|pliers))\b",
            re.IGNORECASE,
        ),
    ),
)

_RETAILER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Amazon", re.compile(r"\bamazon(?:\.com)?\b", re.IGNORECASE)),
    ("Home Depot", re.compile(r"\b(?:the\s+)?home\s*depot\b", re.IGNORECASE)),
    ("Lowe's", re.compile(r"\blowe['’]?s\b", re.IGNORECASE)),
    ("Costco", re.compile(r"\bcostco(?:\.com)?\b", re.IGNORECASE)),
    ("Walmart", re.compile(r"\bwalmart(?:\.com)?\b", re.IGNORECASE)),
    ("Target", re.compile(r"\btarget(?:\.com)?\b", re.IGNORECASE)),
    ("Woot", re.compile(r"\bwoot(?:\.com)?\b", re.IGNORECASE)),
    ("Acme Tools", re.compile(r"\bacme\s*tools?\b", re.IGNORECASE)),
    ("Tool Nut", re.compile(r"\btool\s*nut\b", re.IGNORECASE)),
    ("Harbor Freight", re.compile(r"\bharbor\s*freight\b", re.IGNORECASE)),
    ("Northern Tool", re.compile(r"\bnorthern\s*tool\b", re.IGNORECASE)),
    ("CPO Outlets", re.compile(r"\bcpo\s*outlets?\b", re.IGNORECASE)),
    ("Ace Hardware", re.compile(r"\bace\s*hardware\b", re.IGNORECASE)),
    ("Direct Tools", re.compile(r"\bdirect\s*tools?(?:\s*outlet)?\b", re.IGNORECASE)),
)

_RETAILER_DOMAINS: tuple[tuple[str, str], ...] = (
    ("amazon.com", "Amazon"),
    ("homedepot.com", "Home Depot"),
    ("lowes.com", "Lowe's"),
    ("costco.com", "Costco"),
    ("walmart.com", "Walmart"),
    ("target.com", "Target"),
    ("woot.com", "Woot"),
    ("acmetools.com", "Acme Tools"),
    ("toolnut.com", "Tool Nut"),
    ("harborfreight.com", "Harbor Freight"),
    ("northerntool.com", "Northern Tool"),
    ("cpooutlets.com", "CPO Outlets"),
    ("acehardware.com", "Ace Hardware"),
    ("directtoolsoutlet.com", "Direct Tools"),
)

_SHIPPING_THRESHOLD_RE = re.compile(
    r"(?i)(?:"
    r"(?:free\s+(?:shipping|delivery|s/h)\s+(?:w/|with|on|over|above|at)\s*)"
    r"|(?:orders?\s+(?:of|over|above)?\s*)"
    r"|(?:(?:on|over|above|w/|with)\s+)"
    r")\$\d[\d,]*(?:\.\d{1,2})?\+?"
)
_PLUS_THRESHOLD_RE = re.compile(r"\$\d[\d,]*(?:\.\d{1,2})?\+")
_ORIGINAL_PRICE_RE = re.compile(
    r"(?i)(?:was|reg\.?|regular(?:ly)?(?:\s+price)?|originally|list\s+price|msrp|retail)\s*[:\-]?\s*\$(\d[\d,]*(?:\.\d{1,2})?)"
)
_ORIGINAL_SUFFIX_RE = re.compile(
    r"(?i)\$(\d[\d,]*(?:\.\d{1,2})?)\s+(?:list\s+price|regular\s+price|reg\.?|msrp)"
)
_DOLLAR_OFF_RE = re.compile(
    r"(?i)(?<!up\s)(?<!to\s)\$(\d[\d,]*(?:\.\d{1,2})?)\s+(?:off|price\s+drop|drop|savings)\b"
)
_PERCENT_OFF_RE = re.compile(
    r"(?i)(?:\b(\d{1,2})\s*%\s*off\b|\bsave\s*(\d{1,2})\s*%)"
)
_PRICE_RE = re.compile(r"\$(\d[\d,]*(?:\.\d{1,2})?)")
_DESC_PRICE_RE = re.compile(
    r"(?i)(?:for|at|to|now|sale\s+for|dropping\s+to|discounted\s+to|dropped\s+to)\s+\$(\d[\d,]*(?:\.\d{1,2})?)"
)
_ROUNDUP_TITLE_RE = re.compile(
    r"(?i)(?:\btool\s+deals\b|\bdeals\s+of\s+the\s+day\b|\bblack\s+friday\b.*\bdeals\b)"
)

_PROMO_CODE_RE = re.compile(
    r"""(?i:(?:promo|coupon|discount)?\s*code(?:\s+is|\s*:)?)\s+["']?([A-Z0-9][A-Z0-9_-]{3,23})["']?"""
)
_PROMO_STOPWORDS = frozenset(
    {
        "NONE",
        "FREE",
        "NEEDED",
        "REQUIRED",
        "APPLIED",
        "CHECKOUT",
        "AMAZON",
        "ONLINE",
        "STORE",
        "VALID",
        "ONLY",
        "WITH",
        "FROM",
        "THAT",
        "THIS",
        "YOUR",
        "HERE",
        "OFFER",
        "DEAL",
        "DEALS",
        "SAVE",
        "EXTRA",
        "APPLY",
        "USING",
        "ENTER",
        "SELECT",
        "PRIME",
    }
)

_HOT_KEYWORD_RE = re.compile(
    r"\b(?:clearance|hack|free\s+battery|free\s+tool|bogo|buy\s+one\s+get\s+one|buy\s+1\s+get\s+1|price\s+mistake|glitch)\b",
    re.IGNORECASE,
)

_TRACKING_PARAMS = frozenset({"iref", "ref", "tag", "fbclid", "gclid", "mc_cid", "mc_eid"})
_IMAGE_NOISE_RE = re.compile(r"(?:1x1|pixel|doubleclick|feedburner)", re.IGNORECASE)


_LEADING_PRICE_PREFIX_RE = re.compile(
    r"^\$\d[\d,]*(?:\.\d{1,2})?(?:\s+or\s+\$\d[\d,]*(?:\.\d{1,2})?)?\s*(?:\||,\s*(?:FS|Free\s+Shipping)(?:\s*,\s*(?:Amazon|Home\s*Depot|Lowe['’]?s|Costco|Walmart|Target))?,?)\s*",
    re.IGNORECASE,
)
_TRAILING_SHIPPING_NOISE_RE = re.compile(
    r"\s*\+\s*free\s+(?:s/h|shipping|delivery|store\s+pickup|pickup)(?:\s+.*)?$",
    re.IGNORECASE,
)
_TRAILING_RETAILER_NOISE_RE = re.compile(
    r"\s+(?:@|at|online)\s+(?:amazon(?:\.com)?|(?:the\s+)?home\s*depot|lowe['’]?s|costco|walmart|target|woot|acme\s*tools?|tool\s*nut|harbor\s*freight|northern\s*tool|cpo\s*outlets?|ace\s*hardware|direct\s*tools?(?:\s*outlet)?)(?:\s+\$\d[\d,]*(?:\.\d{1,2})?)?\s*$",
    re.IGNORECASE,
)
_TRAILING_FOR_PRICE_RE = re.compile(
    r"\s+(?:for|at)\s+\$\d[\d,]*(?:\.\d{1,2})?\s*$",
    re.IGNORECASE,
)
_TRAILING_BARE_PRICE_RE = re.compile(
    r"\s+\$\d[\d,]*(?:\.\d{1,2})?(?:\s*\((?:was|reg\.?)\s*\$\d[\d,]*(?:\.\d{1,2})?\))?\s*$",
    re.IGNORECASE,
)


def strip_html(raw: str) -> str:
    """Remove HTML tags, decode HTML entities, and collapse whitespace."""
    if not raw:
        return ""
    no_tags = _TAG_RE.sub(" ", raw)
    unescaped = html.unescape(no_tags)
    return _WHITESPACE_RE.sub(" ", unescaped).strip()


def clean_deal_title(raw_title: str) -> str:
    """Strip redundant RSS price prefixes, shipping suffixes, and retailer tails from deal titles."""
    normalized = strip_html(raw_title)
    if not normalized:
        return ""

    cleaned = _LEADING_PRICE_PREFIX_RE.sub("", normalized).strip()
    for _ in range(3):
        updated = _TRAILING_SHIPPING_NOISE_RE.sub("", cleaned).strip()
        updated = _TRAILING_RETAILER_NOISE_RE.sub("", updated).strip()
        updated = _TRAILING_FOR_PRICE_RE.sub("", updated).strip()
        updated = _TRAILING_BARE_PRICE_RE.sub("", updated).strip()
        if updated == cleaned:
            break
        cleaned = updated

    cleaned = cleaned.rstrip(" -|@:")
    return cleaned if len(cleaned) >= 6 else normalized


def detect_brand(text: str) -> ToolBrand:
    """Detect canonical ToolBrand from deal title/description text."""
    for brand, pattern in _BRAND_PATTERNS:
        if pattern.search(text):
            return brand
    return ToolBrand.OTHER


def detect_retailer(text: str, url: str = "", retailer_hint: str | None = None) -> str:
    """Detect canonical US tool retailer from hint, direct URL, or deal text."""
    if retailer_hint:
        for canonical, pattern in _RETAILER_PATTERNS:
            if pattern.search(retailer_hint):
                return canonical

    host = urlparse(url).netloc.lower()
    for domain, canonical in _RETAILER_DOMAINS:
        if host == domain or host.endswith(f".{domain}"):
            return canonical

    best_retailer: str | None = None
    best_start: int | None = None
    for canonical, pattern in _RETAILER_PATTERNS:
        match = pattern.search(text)
        if match and (best_start is None or match.start() < best_start):
            best_start = match.start()
            best_retailer = canonical

    return best_retailer or "Other"


def _parse_amount(raw: str) -> float | None:
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    if value <= 0 or value > 50000:
        return None
    return round(value, 2)


def _strip_non_sale_price_phrases(text: str) -> str:
    cleaned = _SHIPPING_THRESHOLD_RE.sub(" ", text)
    cleaned = _PLUS_THRESHOLD_RE.sub(" ", cleaned)
    cleaned = _ORIGINAL_PRICE_RE.sub(" ", cleaned)
    cleaned = _ORIGINAL_SUFFIX_RE.sub(" ", cleaned)
    cleaned = _DOLLAR_OFF_RE.sub(" ", cleaned)
    return cleaned


def extract_prices_and_discount(
    title: str,
    description: str = "",
    price_hint: float | None = None,
) -> tuple[float | None, float | None, int | None]:
    """Extract currentPrice, originalPrice, and discountPercent from deal text."""
    combined = f"{title} {description}".strip()

    original_price: float | None = None
    for regex in (_ORIGINAL_PRICE_RE, _ORIGINAL_SUFFIX_RE):
        match = regex.search(combined)
        if match:
            original_price = _parse_amount(match.group(1))
            if original_price is not None:
                break

    dollar_off: float | None = None
    off_match = _DOLLAR_OFF_RE.search(combined)
    if off_match:
        dollar_off = _parse_amount(off_match.group(1))

    explicit_pct: int | None = None
    pct_match = _PERCENT_OFF_RE.search(combined)
    if pct_match:
        pct_raw = pct_match.group(1) or pct_match.group(2)
        if pct_raw:
            pct_val = int(pct_raw)
            if 1 <= pct_val <= 99:
                explicit_pct = pct_val

    current_price: float | None = None
    if price_hint is not None and 0 < price_hint <= 50000:
        current_price = round(float(price_hint), 2)
    else:
        clean_title = _strip_non_sale_price_phrases(title)
        title_match = _PRICE_RE.search(clean_title)
        if title_match:
            current_price = _parse_amount(title_match.group(1))
        elif description:
            clean_desc = _strip_non_sale_price_phrases(description)
            desc_match = _DESC_PRICE_RE.search(clean_desc)
            if desc_match:
                current_price = _parse_amount(desc_match.group(1))

    if original_price is None and current_price is not None and dollar_off is not None:
        original_price = round(current_price + dollar_off, 2)

    if original_price is not None and current_price is not None and original_price <= current_price:
        original_price = None

    discount_percent: int | None = None
    if current_price is not None and original_price is not None and original_price > current_price:
        computed = round((original_price - current_price) / original_price * 100)
        if 1 <= computed <= 99:
            discount_percent = computed
    elif explicit_pct is not None:
        discount_percent = explicit_pct

    return current_price, original_price, discount_percent


def extract_promo_code(text: str) -> str | None:
    """Extract an uppercase alphanumeric promo/coupon code if present."""
    for match in _PROMO_CODE_RE.finditer(text):
        candidate = match.group(1).strip()
        if candidate in _PROMO_STOPWORDS:
            continue
        if not re.search(r"[A-Z0-9]", candidate):
            continue
        return candidate
    return None


def extract_image_url(item: RawFeedItem) -> str | None:
    """Extract a valid HTTP(S) image URL from feed metadata or embedded HTML."""
    if item.image_url and item.image_url.startswith(("http://", "https://")):
        if not _IMAGE_NOISE_RE.search(item.image_url):
            return item.image_url

    for html_blob in (item.description, item.content):
        if not html_blob:
            continue
        for match in _IMG_SRC_RE.finditer(html_blob):
            candidate = html.unescape(match.group(1)).strip()
            if candidate.startswith(("http://", "https://")) and not _IMAGE_NOISE_RE.search(candidate):
                return candidate
    return None


def compute_is_hot(
    discount_percent: int | None,
    title: str,
    description: str = "",
) -> bool:
    """Determine whether a deal qualifies as a hot alert-worthy deal."""
    if discount_percent is not None and discount_percent >= 30:
        return True
    return bool(_HOT_KEYWORD_RE.search(f"{title} {description}"))


def _normalize_url_for_id(url: str) -> str:
    parsed = urlparse(url.strip())
    filtered_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            urlencode(filtered_query),
            "",
        )
    )


def generate_deal_id(deal_url: str, title: str) -> str:
    """Generate a canonical 16-char SHA-256 hex ID from normalized URL and title."""
    norm_url = _normalize_url_for_id(deal_url)
    norm_title = _WHITESPACE_RE.sub(" ", title).strip().lower()
    digest = hashlib.sha256(f"{norm_url}|{norm_title}".encode("utf-8")).hexdigest()
    return digest[:16]


def normalize_published_at(pub_date: str) -> str:
    """Convert RFC 2822 or ISO 8601 publication timestamps to UTC ISO-8601 Z format."""
    cleaned = pub_date.strip()
    if cleaned:
        dt: datetime | None = None
        try:
            dt = parsedate_to_datetime(cleaned)
        except (TypeError, ValueError):
            try:
                dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            except ValueError:
                dt = None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_deal(item: RawFeedItem) -> ParsedDeal:
    """Convert a RawFeedItem into a normalized ParsedDeal."""
    clean_title = strip_html(item.title)
    clean_desc = strip_html(item.description)
    clean_content = strip_html(item.content)
    combined_text = " ".join(part for part in (clean_title, clean_desc, clean_content) if part)

    brand = detect_brand(f"{clean_title} {clean_desc}")
    retailer = detect_retailer(
        f"{clean_title} {clean_desc}",
        url=item.link,
        retailer_hint=item.retailer_hint,
    )
    if retailer == "Other" and clean_content:
        retailer = detect_retailer(
            clean_content[:1200],
            url=item.link,
            retailer_hint=item.retailer_hint,
        )

    is_roundup = bool(_ROUNDUP_TITLE_RE.search(clean_title))
    has_explicit_title_price = bool(_PRICE_RE.search(_strip_non_sale_price_phrases(clean_title)))
    effective_price_hint = None if (is_roundup and not has_explicit_title_price) else item.price_hint

    current_price, original_price, discount_percent = extract_prices_and_discount(
        clean_title,
        "" if (is_roundup and not has_explicit_title_price) else clean_desc,
        price_hint=effective_price_hint,
    )
    if is_roundup and not has_explicit_title_price:
        current_price = None
        original_price = None
    if current_price is None and clean_content and not is_roundup:
        current_price, content_orig, content_disc = extract_prices_and_discount(
            clean_title,
            clean_content[:800],
            price_hint=effective_price_hint,
        )
        if original_price is None:
            original_price = content_orig
        if discount_percent is None:
            discount_percent = content_disc
    promo_code = extract_promo_code(combined_text)
    image_url = extract_image_url(item)
    published_at = normalize_published_at(item.pub_date)
    is_hot = compute_is_hot(discount_percent, clean_title, clean_desc)
    deal_id = generate_deal_id(item.link, clean_title)
    display_title = clean_deal_title(clean_title)

    return ParsedDeal(
        id=deal_id,
        title=display_title,
        brand=brand,
        retailer=retailer,
        currentPrice=current_price,
        originalPrice=original_price,
        discountPercent=discount_percent,
        promoCode=promo_code,
        dealUrl=item.link.strip(),
        imageUrl=image_url,
        source=item.source,
        publishedAt=published_at,
        isHot=is_hot,
    )
