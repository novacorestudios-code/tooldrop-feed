"""Unit tests for bot.rss_sources, bot.fcm_notifier, and bot.generate_feed (TDD-first)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401
from bot.fcm_notifier import notify_hot_deals
from bot.generate_feed import generate_feed
from bot.models import ParsedDeal, RawFeedItem, ToolBrand
from bot.rss_sources import FeedSource, fetch_all_feeds, is_tool_deal, parse_feed_xml

SLICKDEALS_SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Slickdeals Tools</title>
    <item>
      <title>DeWalt 20V MAX XR Impact Driver Kit $129 (Was $199) at Amazon</title>
      <link>https://www.amazon.com/dp/B07D53T76M?psc=1</link>
      <description>Amazon has DeWalt 20V MAX XR Kit for $129, reg $199. Use promo code DEWALT20.</description>
      <content:encoded><![CDATA[<img src="https://static.slickdealscdn.com/dewalt.jpg" />]]></content:encoded>
      <pubDate>Thu, 19 Nov 2026 15:30:00 +0000</pubDate>
    </item>
    <item>
      <title>50"x60" Bedsure Sherpa Fleece Throw Blanket $9.59</title>
      <link>https://slickdeals.net/f/999999</link>
      <description>Throw blanket deal on Amazon.</description>
      <pubDate>Thu, 19 Nov 2026 15:20:00 +0000</pubDate>
    </item>
    <item>
      <title>LEGO Creator 3-in-1 Robot Set $27</title>
      <link>https://slickdeals.net/f/888888</link>
      <description>Great toy deal.</description>
      <pubDate>Thu, 19 Nov 2026 15:10:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

DEALNEWS_SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dealnews="https://www.dealnews.com/ns/rss/1.0.htm" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>DealNews Tools</title>
    <item>
      <title>Milwaukee M18 FUEL Track Saw Kit for $349 + free shipping</title>
      <link>https://www.dealnews.com/Milwaukee-M18/12345.html?iref=rss-c196</link>
      <description>&lt;p&gt;At Home Depot, $150 off list price of $499.&lt;/p&gt;</description>
      <pubDate>Thu, 19 Nov 2026 10:00:00 -0500</pubDate>
      <dealnews:retailer>Home Depot</dealnews:retailer>
      <dealnews:category>Tools &amp; Hardware</dealnews:category>
      <dealnews:price currency="USD">349.00</dealnews:price>
      <media:content url="https://d.dlnws.com/m18-track-saw.jpg" medium="image" />
    </item>
    <item>
      <title>Roborock F25 Wet Dry Vacuum Mop Combo for $230</title>
      <link>https://www.dealnews.com/Roborock/999.html</link>
      <description>&lt;p&gt;Robot vacuum mop combo.&lt;/p&gt;</description>
      <pubDate>Thu, 19 Nov 2026 09:50:00 -0500</pubDate>
      <dealnews:retailer>Amazon</dealnews:retailer>
      <dealnews:category>Vacuum Cleaners</dealnews:category>
      <dealnews:price currency="USD">230.00</dealnews:price>
    </item>
    <item>
      <title>Ring Indoor Cam Plus 2K Security Camera for $25</title>
      <link>https://www.dealnews.com/Ring-Cam/777.html</link>
      <description>&lt;p&gt;Smart security camera.&lt;/p&gt;</description>
      <pubDate>Thu, 19 Nov 2026 09:40:00 -0500</pubDate>
      <dealnews:retailer>Amazon</dealnews:retailer>
      <dealnews:category>Security Cameras</dealnews:category>
      <dealnews:price currency="USD">25.00</dealnews:price>
    </item>
  </channel>
</rss>
"""


class TestRssSourcesAndGenerateFeed(unittest.TestCase):
    """Tests for feed filtering, resilience, FCM safety, and JSON feed output."""

    def test_parse_feed_xml_and_filter_excludes_non_tools(self) -> None:
        sd_items = parse_feed_xml(SLICKDEALS_SAMPLE_XML, source_name="Slickdeals")
        dn_items = parse_feed_xml(DEALNEWS_SAMPLE_XML, source_name="DealNews")

        filtered_sd = [item for item in sd_items if is_tool_deal(item)]
        filtered_dn = [item for item in dn_items if is_tool_deal(item)]

        self.assertEqual(len(filtered_sd), 1)
        self.assertIn("DeWalt", filtered_sd[0].title)
        self.assertEqual(len(filtered_dn), 1)
        self.assertIn("Milwaukee", filtered_dn[0].title)
        self.assertEqual(filtered_dn[0].image_url, "https://d.dlnws.com/m18-track-saw.jpg")
        self.assertEqual(filtered_dn[0].price_hint, 349.0)

    def test_filter_excludes_toolguyd_non_deal_news_articles(self) -> None:
        toolguyd_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>It Looks Like SawStop WorkTrack Ripped off JessEm Table Saw Guides</title>
              <link>https://toolguyd.com/sawstop-ripoff/</link>
              <category>New Tools</category>
              <description>Editorial commentary.</description>
            </item>
            <item>
              <title>These Dewalt Tool Deals are HOT at Tool Nut Right Now</title>
              <link>https://toolguyd.com/dewalt-tool-nut/</link>
              <category>Tool Deals</category>
              <description>Save big on DeWalt kits.</description>
            </item>
          </channel>
        </rss>
        """
        items = parse_feed_xml(toolguyd_xml, source_name="ToolGuyd")
        filtered = [it for it in items if is_tool_deal(it)]
        self.assertEqual(len(filtered), 1)
        self.assertIn("Dewalt", filtered[0].title)

    def test_filter_excludes_stanley_tumblers_and_drinkware(self) -> None:
        tumbler = RawFeedItem(
            title="Stanley 40-oz H2.0 Flowstate Quencher Tumbler for $20 + free shipping",
            link="https://www.dealnews.com/Stanley-Tumbler/1.html",
            description="Insulated stainless steel drinkware tumbler.",
            source="DealNews",
            categories=("Tools & Hardware",),
        )
        self.assertFalse(is_tool_deal(tumbler))

    def test_fetch_all_feeds_isolates_failing_sources(self) -> None:
        sources = (
            FeedSource(name="BrokenFeed", url="https://broken.invalid/rss"),
            FeedSource(name="Slickdeals", url="https://slickdeals.example/rss"),
        )

        def fake_fetch(url: str, timeout: int = 10) -> bytes:
            if "broken" in url:
                raise TimeoutError("timed out")
            return SLICKDEALS_SAMPLE_XML

        items = fetch_all_feeds(sources=sources, fetcher=fake_fetch)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "Slickdeals")

    def test_fcm_notifier_sends_brand_topic_for_new_hot_deal_when_configured(self) -> None:
        hot_deal = ParsedDeal(
            id="0123456789abcdef",
            title="Milwaukee M18 Hack",
            brand=ToolBrand.MILWAUKEE,
            retailer="Home Depot",
            currentPrice=149.0,
            originalPrice=249.0,
            discountPercent=40,
            promoCode=None,
            dealUrl="https://www.homedepot.com/p/1",
            imageUrl=None,
            source="Slickdeals",
            publishedAt="2026-11-19T15:00:00Z",
            isHot=True,
        )
        old_hot_deal = ParsedDeal(
            id="fedcba9876543210",
            title="Old Hot Deal",
            brand=ToolBrand.DEWALT,
            retailer="Amazon",
            currentPrice=99.0,
            originalPrice=199.0,
            discountPercent=50,
            promoCode=None,
            dealUrl="https://www.amazon.com/dp/B000000000",
            imageUrl=None,
            source="Slickdeals",
            publishedAt="2026-11-18T15:00:00Z",
            isHot=True,
        )
        sent_messages: list[tuple[str, str, dict[str, object]]] = []
        fake_sa = json.dumps(
            {
                "project_id": "tooldrop-test",
                "client_email": "bot@tooldrop-test.iam.gserviceaccount.com",
                "private_key": "fake-key",
            }
        )

        sent = notify_hot_deals(
            [hot_deal, old_hot_deal],
            previous_ids={"fedcba9876543210"},
            service_account_json=fake_sa,
            token_provider=lambda _sa: "fake-oauth-token",
            message_sender=lambda proj, tok, body: sent_messages.append((proj, tok, body)),
        )

        self.assertEqual(sent, 1)
        self.assertEqual(len(sent_messages), 1)
        proj, tok, body = sent_messages[0]
        self.assertEqual(proj, "tooldrop-test")
        self.assertEqual(tok, "fake-oauth-token")
        self.assertEqual(body["message"]["topic"], "brand_milwaukee")

    def test_fcm_notifier_is_safe_noop_when_env_unset(self) -> None:
        hot_deal = ParsedDeal(
            id="0123456789abcdef",
            title="Milwaukee M18 Hack",
            brand=ToolBrand.MILWAUKEE,
            retailer="Home Depot",
            currentPrice=149.0,
            originalPrice=249.0,
            discountPercent=40,
            promoCode=None,
            dealUrl="https://www.homedepot.com/p/1",
            imageUrl=None,
            source="Slickdeals",
            publishedAt="2026-11-19T15:00:00Z",
            isHot=True,
        )
        with patch.dict(os.environ, {"FIREBASE_SERVICE_ACCOUNT_JSON": ""}, clear=False):
            sent = notify_hot_deals([hot_deal], previous_ids=set())
        self.assertEqual(sent, 0)

    def test_generate_feed_writes_valid_schema_and_rewrites_amazon_links(self) -> None:
        sources = (
            FeedSource(name="Slickdeals", url="https://slickdeals.example/rss"),
            FeedSource(name="DealNews", url="https://dealnews.example/rss"),
        )

        def fake_fetch(url: str, timeout: int = 10) -> bytes:
            if "slickdeals" in url:
                return SLICKDEALS_SAMPLE_XML
            return DEALNEWS_SAMPLE_XML

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "public" / "deals_feed.json"
            with patch.dict(os.environ, {"FIREBASE_SERVICE_ACCOUNT_JSON": ""}, clear=False):
                payload = generate_feed(
                    output_path=out_path,
                    sources=sources,
                    fetcher=fake_fetch,
                    associate_tag="tooldrop-20",
                )

            self.assertEqual(payload.schemaVersion, 1)
            self.assertEqual(payload.dealsCount, 2)
            self.assertTrue(out_path.exists())

            saved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schemaVersion"], 1)
            self.assertEqual(saved["dealsCount"], 2)
            self.assertTrue(saved["generatedAt"].endswith("Z"))
            self.assertEqual(len(saved["deals"]), 2)

            expected_keys = {
                "id",
                "title",
                "brand",
                "retailer",
                "currentPrice",
                "originalPrice",
                "discountPercent",
                "promoCode",
                "dealUrl",
                "imageUrl",
                "source",
                "publishedAt",
                "isHot",
            }
            for deal in saved["deals"]:
                self.assertEqual(set(deal.keys()), expected_keys)

            amazon_deals = [d for d in saved["deals"] if d["retailer"] == "Amazon"]
            self.assertEqual(len(amazon_deals), 1)
            self.assertIn("tag=tooldrop-20", amazon_deals[0]["dealUrl"])

    def test_generate_feed_ranks_priced_deals_ahead_of_unpriced_roundups(self) -> None:
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>DeWalt Black Friday Tool Deals Roundup</title>
              <link>https://toolguyd.com/dewalt-roundup/</link>
              <category>Tool Deals</category>
              <description>Save up to 40% off select DeWalt tools.</description>
              <pubDate>Thu, 19 Nov 2026 16:00:00 +0000</pubDate>
            </item>
            <item>
              <title>Milwaukee M18 FUEL Impact Driver $99 at Home Depot</title>
              <link>https://www.homedepot.com/p/milwaukee/123</link>
              <description>Regular price $149, on sale for $99.</description>
              <pubDate>Thu, 19 Nov 2026 15:00:00 +0000</pubDate>
            </item>
          </channel>
        </rss>
        """
        sources = (FeedSource(name="ToolGuyd", url="https://toolguyd.example/rss"),)
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "deals_feed.json"
            with patch.dict(os.environ, {"FIREBASE_SERVICE_ACCOUNT_JSON": ""}, clear=False):
                payload = generate_feed(
                    output_path=out_path,
                    sources=sources,
                    fetcher=lambda _u, _t=10: xml,
                )
        self.assertEqual(len(payload.deals), 2)
        self.assertEqual(payload.deals[0].currentPrice, 99.0)
        self.assertIsNone(payload.deals[1].currentPrice)

    def test_generate_feed_preserves_existing_file_when_all_feeds_fail(self) -> None:
        sources = (FeedSource(name="Broken", url="https://broken.invalid/rss"),)

        def failing_fetch(url: str, timeout: int = 10) -> bytes:
            raise OSError("network unreachable")

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "public" / "deals_feed.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            existing_payload = {
                "schemaVersion": 1,
                "generatedAt": "2026-11-18T12:00:00Z",
                "dealsCount": 1,
                "deals": [
                    {
                        "id": "abcdef0123456789",
                        "title": "Existing DeWalt Kit",
                        "brand": "DEWALT",
                        "retailer": "Amazon",
                        "currentPrice": 99.0,
                        "originalPrice": 149.0,
                        "discountPercent": 34,
                        "promoCode": None,
                        "dealUrl": "https://www.amazon.com/dp/B07D53T76M",
                        "imageUrl": None,
                        "source": "Slickdeals",
                        "publishedAt": "2026-11-18T12:00:00Z",
                        "isHot": True,
                    }
                ],
            }
            out_path.write_text(json.dumps(existing_payload), encoding="utf-8")

            result = generate_feed(
                output_path=out_path,
                sources=sources,
                fetcher=failing_fetch,
            )

            self.assertEqual(result.dealsCount, 1)
            persisted = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted, existing_payload)


if __name__ == "__main__":
    unittest.main()
