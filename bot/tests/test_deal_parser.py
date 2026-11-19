"""Unit tests for bot.deal_parser (written TDD-first)."""

from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401
from bot.deal_parser import (
    clean_deal_title,
    compute_is_hot,
    detect_brand,
    detect_retailer,
    extract_image_url,
    extract_prices_and_discount,
    extract_promo_code,
    generate_deal_id,
    parse_deal,
    strip_html,
)
from bot.models import RawFeedItem, ToolBrand


class TestDealParserHelpers(unittest.TestCase):
    """Tests for individual parsing helpers in deal_parser."""

    def test_strip_html_removes_tags_and_decodes_entities(self) -> None:
        raw = "<p>DeWalt &amp; Milwaukee <b>20V</b>&#x20;Drill &quot;Kit&quot; &nbsp; $99</p>"
        self.assertEqual(strip_html(raw), 'DeWalt & Milwaukee 20V Drill "Kit" $99')

    def test_detect_brand_maps_all_supported_brands(self) -> None:
        cases = [
            ("DeWalt 20V MAX XR Brushless Impact Driver", ToolBrand.DEWALT),
            ("Milwaukee M18 FUEL Hammer Drill Kit", ToolBrand.MILWAUKEE),
            ("Makita 18V LXT Lithium-Ion Cordless Grinder", ToolBrand.MAKITA),
            ("RYOBI ONE+ HP 18V Brushless Jigsaw", ToolBrand.RYOBI),
            ("Bosch 18V Profactor Rotary Hammer", ToolBrand.BOSCH),
            ("Craftsman V20 Cordless Brad Nailer", ToolBrand.CRAFTSMAN),
            ("RIDGID 18V SubCompact Brushless Combo", ToolBrand.RIDGID),
            ("Harbor Freight Bauer 20V Cordless Sander Deal", ToolBrand.HARBOR_FREIGHT),
            ("Hercules 20V Brushless Impact Wrench", ToolBrand.HARBOR_FREIGHT),
            ("Kobalt 302-Piece Mechanics Tool Set", ToolBrand.OTHER),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_brand(text), expected)

    def test_detect_retailer_prioritizes_hint_domain_and_text(self) -> None:
        self.assertEqual(
            detect_retailer(
                "Milwaukee M18 Kit at Home Depot",
                "https://slickdeals.net/f/12345",
            ),
            "Home Depot",
        )
        self.assertEqual(
            detect_retailer(
                "DeWalt Sander Deal",
                "https://www.amazon.com/dp/B09Y9G25CY",
            ),
            "Amazon",
        )
        self.assertEqual(
            detect_retailer(
                "Lowe's 1-Day Sale on Construction Jack",
                "https://toolguyd.com/lowes-sale/",
            ),
            "Lowe's",
        )
        self.assertEqual(
            detect_retailer(
                "These Dewalt Tool Deals are HOT at Tool Nut Right Now",
                "https://toolguyd.com/dewalt-tool-nut/",
            ),
            "Tool Nut",
        )
        self.assertEqual(
            detect_retailer(
                "Acme Tools Milwaukee Sale",
                "https://toolguyd.com/acme/",
            ),
            "Acme Tools",
        )
        self.assertEqual(
            detect_retailer(
                "Woot Specialty Tools Sale",
                "https://www.dealnews.com/woot/123",
            ),
            "Woot",
        )
        self.assertEqual(
            detect_retailer(
                "Harbor Freight Black Friday Tool Deals",
                "https://toolguyd.com/hf/",
            ),
            "Harbor Freight",
        )
        self.assertEqual(
            detect_retailer(
                "CRAFTSMAN 2500 Watt Inverter Generator online Costco $299",
                "https://slickdeals.net/f/20345805",
            ),
            "Costco",
        )
        self.assertEqual(
            detect_retailer(
                "Hart 20V Drill Kit Rollback",
                "https://www.walmart.com/ip/123456",
            ),
            "Walmart",
        )
        self.assertEqual(
            detect_retailer(
                "DeWalt Cordless Kit at Target",
                "https://slickdeals.net/f/999",
            ),
            "Target",
        )
        self.assertEqual(
            detect_retailer(
                "Ironton Air Compressor at Northern Tool",
                "https://www.northerntool.com/shop/tools/1",
            ),
            "Northern Tool",
        )
        self.assertEqual(
            detect_retailer(
                "Makita Reconditioned Combo at CPO Outlets",
                "https://www.cpooutlets.com/makita-kit",
            ),
            "CPO Outlets",
        )
        self.assertEqual(
            detect_retailer(
                "Craftsman Mechanics Set at Ace Hardware",
                "https://www.acehardware.com/123",
            ),
            "Ace Hardware",
        )
        self.assertEqual(
            detect_retailer(
                "Ryobi Factory Blemished Kit at Direct Tools",
                "https://www.directtoolsoutlet.com/product/123",
            ),
            "Direct Tools",
        )
        self.assertEqual(
            detect_retailer(
                "Local Hardware Clearance",
                "https://example.com/deal",
            ),
            "Other",
        )

    def test_clean_deal_title_strips_rss_price_and_shipping_noise(self) -> None:
        self.assertEqual(
            clean_deal_title('$6.24 | 2-Piece DeWALT Double Ended Nut Driver Set (1/4" + 3/8") at Amazon'),
            '2-Piece DeWALT Double Ended Nut Driver Set (1/4" + 3/8")',
        )
        self.assertEqual(
            clean_deal_title("Milwaukee 600 Lumens LED REDLITHIUM Hard Hat Headlamp (2-Pack) + Free S/H @ Home Depot $99"),
            "Milwaukee 600 Lumens LED REDLITHIUM Hard Hat Headlamp (2-Pack)",
        )
        self.assertEqual(
            clean_deal_title("Ecarke 112W 11200-Lumen LED Work Light for DeWalt 20V Batteries for $15 + free shipping w/ Prime"),
            "Ecarke 112W 11200-Lumen LED Work Light for DeWalt 20V Batteries",
        )
        self.assertEqual(
            clean_deal_title("CRAFTSMAN 2500 Watt Inverter Generator online Costco $299"),
            "CRAFTSMAN 2500 Watt Inverter Generator",
        )

    def test_parse_deal_ignores_roundup_price_hint_noise(self) -> None:
        raw = RawFeedItem(
            title="Fluke Black Friday Deals at Amazon: Up to 43% off + free shipping w/ Prime",
            link="https://www.dealnews.com/Fluke-Black-Friday/123.html",
            description="Select Fluke meters on sale for $30 and up.",
            price_hint=30.0,
            pub_date="Thu, 19 Nov 2026 14:00:00 +0000",
            source="DealNews",
        )
        deal = parse_deal(raw)
        self.assertEqual(deal.retailer, "Amazon")
        self.assertIsNone(deal.currentPrice)
        self.assertEqual(deal.discountPercent, 43)

    def test_extract_prices_and_discount_handles_was_and_ignores_shipping_threshold(self) -> None:
        cur, orig, disc = extract_prices_and_discount(
            "Bosch 9-Piece Hole Saw Kit $31.99 + Free Shipping w/ Prime or on $35+",
            "Regular price $49.99, on sale for $31.99.",
        )
        self.assertEqual(cur, 31.99)
        self.assertEqual(orig, 49.99)
        self.assertEqual(disc, 36)

    def test_extract_prices_and_discount_handles_price_drop_and_percent_off(self) -> None:
        cur, orig, disc = extract_prices_and_discount(
            "Kobalt 302-Piece Mechanics Tool Set for $99 + free shipping",
            "Dropped to $99 in Lowe's sale. That's a $50 price drop.",
        )
        self.assertEqual(cur, 99.0)
        self.assertEqual(orig, 149.0)
        self.assertEqual(disc, 34)

        cur2, orig2, disc2 = extract_prices_and_discount(
            "DeWalt Black Friday Deals at Amazon: Up to 45% off",
            "Save up to 45% off select kits.",
        )
        self.assertIsNone(cur2)
        self.assertIsNone(orig2)
        self.assertEqual(disc2, 45)

        cur3, orig3, disc3 = extract_prices_and_discount(
            "Milwaukee 600 Lumens LED REDLITHIUM Hard Hat Headlamp (2-Pack) + Free S/H @ Home Depot $99",
            "Available for ship to store and free delivery",
        )
        self.assertEqual(cur3, 99.0)
        self.assertIsNone(orig3)
        self.assertIsNone(disc3)

    def test_parse_deal_uses_content_fallback_for_single_tool_deal(self) -> None:
        raw = RawFeedItem(
            title="Lower Pricing on this Dewalt ToughSystem 3-Drawer Tool Box",
            link="https://toolguyd.com/dewalt-toughsystem/",
            description="Great tool box at a great price.",
            content="<p>This Dewalt ToughSystem 2.0 3-drawer tool box has been discounted to $99 at Acme Tools. Use code TOOLGUYD.</p>",
            pub_date="Fri, 13 Nov 2026 21:22:50 +0000",
            source="ToolGuyd",
        )
        deal = parse_deal(raw)
        self.assertEqual(deal.brand, ToolBrand.DEWALT)
        self.assertEqual(deal.retailer, "Acme Tools")
        self.assertEqual(deal.currentPrice, 99.0)
        self.assertEqual(deal.promoCode, "TOOLGUYD")

    def test_extract_promo_code_detects_valid_codes_and_ignores_noise(self) -> None:
        self.assertEqual(
            extract_promo_code('Apply coupon code "ZYU3E9LV" at checkout.'),
            "ZYU3E9LV",
        )
        self.assertEqual(
            extract_promo_code("Use promo code TOOL25OFF for extra savings."),
            "TOOL25OFF",
        )
        self.assertIsNone(
            extract_promo_code("No code needed for this deal at checkout."),
        )

    def test_extract_image_url_prefers_explicit_then_html_img(self) -> None:
        item_direct = RawFeedItem(
            title="Test",
            link="https://example.com/1",
            image_url="https://cdn.example.com/img.jpg",
            description='<img src="https://other.example.com/other.jpg">',
        )
        self.assertEqual(extract_image_url(item_direct), "https://cdn.example.com/img.jpg")

        item_html = RawFeedItem(
            title="Test",
            link="https://example.com/2",
            content='<div><img src="https://static.slickdealscdn.com/test.jpg" width="300"></div>',
        )
        self.assertEqual(
            extract_image_url(item_html),
            "https://static.slickdealscdn.com/test.jpg",
        )

    def test_compute_is_hot_triggers_on_discount_or_keywords(self) -> None:
        self.assertTrue(compute_is_hot(30, "DeWalt Drill Kit"))
        self.assertTrue(compute_is_hot(10, "Milwaukee M18 Clearance Sale"))
        self.assertTrue(compute_is_hot(None, "Ryobi Combo Hack + Free Battery"))
        self.assertTrue(compute_is_hot(15, "Home Depot BOGO Tool Event"))
        self.assertFalse(compute_is_hot(20, "Makita 18V Impact Driver"))
        self.assertFalse(compute_is_hot(None, "Bosch Drill Bit Set"))

    def test_generate_deal_id_is_deterministic_16_char_hex_and_strips_utm(self) -> None:
        id1 = generate_deal_id(
            "https://slickdeals.net/f/12345-dewalt-kit?utm_source=rss&utm_medium=RSS2",
            "DeWalt 20V Kit",
        )
        id2 = generate_deal_id(
            "https://slickdeals.net/f/12345-dewalt-kit/",
            "  dewalt   20v kit ",
        )
        self.assertEqual(len(id1), 16)
        self.assertEqual(id1, id2)
        self.assertRegex(id1, r"^[0-9a-f]{16}$")

    def test_parse_deal_end_to_end_produces_valid_parsed_deal(self) -> None:
        raw = RawFeedItem(
            title="Milwaukee M18 FUEL Hammer Drill Kit $179 (Was $299) + Free Battery",
            link="https://www.homedepot.com/p/Milwaukee-M18/12345?utm_source=rss",
            description='<p>At Home Depot, get <b>$120 off</b> using promo code MILW15.</p><img src="https://images.homedepot.com/m18.jpg">',
            pub_date="Thu, 19 Nov 2026 15:43:01 +0000",
            source="Slickdeals",
        )
        deal = parse_deal(raw)
        self.assertEqual(deal.brand, ToolBrand.MILWAUKEE)
        self.assertEqual(deal.retailer, "Home Depot")
        self.assertEqual(deal.currentPrice, 179.0)
        self.assertEqual(deal.originalPrice, 299.0)
        self.assertEqual(deal.discountPercent, 40)
        self.assertEqual(deal.promoCode, "MILW15")
        self.assertEqual(deal.imageUrl, "https://images.homedepot.com/m18.jpg")
        self.assertEqual(deal.source, "Slickdeals")
        self.assertEqual(deal.publishedAt, "2026-11-19T15:43:01Z")
        self.assertTrue(deal.isHot)
        self.assertEqual(len(deal.id), 16)


if __name__ == "__main__":
    unittest.main()
