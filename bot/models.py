"""Domain models and feed schema definitions for ToolDrop bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolBrand(str, Enum):
    """Canonical power tool brands supported by ToolDrop."""

    DEWALT = "DEWALT"
    MILWAUKEE = "MILWAUKEE"
    MAKITA = "MAKITA"
    RYOBI = "RYOBI"
    BOSCH = "BOSCH"
    CRAFTSMAN = "CRAFTSMAN"
    RIDGID = "RIDGID"
    HARBOR_FREIGHT = "HARBOR_FREIGHT"
    OTHER = "OTHER"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class RawFeedItem:
    """Normalized raw entry extracted from an RSS or Atom feed."""

    title: str
    link: str
    description: str = ""
    content: str = ""
    pub_date: str = ""
    source: str = ""
    image_url: str | None = None
    retailer_hint: str | None = None
    price_hint: float | None = None
    categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedDeal:
    """Normalized power tool deal matching the public JSON feed contract."""

    id: str
    title: str
    brand: ToolBrand
    retailer: str
    currentPrice: float | None
    originalPrice: float | None
    discountPercent: int | None
    promoCode: str | None
    dealUrl: str
    imageUrl: str | None
    source: str
    publishedAt: str
    isHot: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize deal to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "brand": self.brand.value,
            "retailer": self.retailer,
            "currentPrice": self.currentPrice,
            "originalPrice": self.originalPrice,
            "discountPercent": self.discountPercent,
            "promoCode": self.promoCode,
            "dealUrl": self.dealUrl,
            "imageUrl": self.imageUrl,
            "source": self.source,
            "publishedAt": self.publishedAt,
            "isHot": self.isHot,
        }


@dataclass(frozen=True)
class DealsFeedPayload:
    """Top-level payload written to public/deals_feed.json."""

    schemaVersion: int = 1
    generatedAt: str = ""
    dealsCount: int = 0
    deals: list[ParsedDeal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize full feed payload to a JSON-compatible dictionary."""
        return {
            "schemaVersion": self.schemaVersion,
            "generatedAt": self.generatedAt,
            "dealsCount": self.dealsCount,
            "deals": [deal.to_dict() for deal in self.deals],
        }
