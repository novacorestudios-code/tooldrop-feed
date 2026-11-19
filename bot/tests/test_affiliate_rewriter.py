"""Unit tests for bot.affiliate_rewriter (written TDD-first)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import _bootstrap  # noqa: F401
from bot.affiliate_rewriter import rewrite_affiliate_url


class TestAffiliateRewriter(unittest.TestCase):
    """Tests for Amazon Associates URL rewriting."""

    def test_adds_tag_to_amazon_url_preserving_asin_and_query(self) -> None:
        url = "https://www.amazon.com/DEWALT-Impact-Driver/dp/B07D53T76M?psc=1&th=1"
        rewritten = rewrite_affiliate_url(url, associate_tag="tooldrop-20")
        self.assertEqual(
            rewritten,
            "https://www.amazon.com/DEWALT-Impact-Driver/dp/B07D53T76M?psc=1&th=1&tag=tooldrop-20",
        )

    def test_replaces_existing_tag_on_bare_amazon_host(self) -> None:
        url = "https://amazon.com/dp/B09Y9G25CY?tag=other-20&ref=sr_1"
        rewritten = rewrite_affiliate_url(url, associate_tag="mytag-20")
        self.assertEqual(
            rewritten,
            "https://amazon.com/dp/B09Y9G25CY?ref=sr_1&tag=mytag-20",
        )

    def test_reads_tag_from_environment_variable_when_not_passed(self) -> None:
        url = "https://www.amazon.com/dp/B09Y9G25CY"
        with patch.dict(os.environ, {"AMAZON_ASSOCIATE_TAG": "envtag-20"}, clear=False):
            rewritten = rewrite_affiliate_url(url)
        self.assertEqual(rewritten, "https://www.amazon.com/dp/B09Y9G25CY?tag=envtag-20")

    def test_leaves_amazon_url_untouched_when_tag_is_empty_or_unset(self) -> None:
        url = "https://www.amazon.com/dp/B09Y9G25CY?psc=1"
        with patch.dict(os.environ, {"AMAZON_ASSOCIATE_TAG": "   "}, clear=False):
            self.assertEqual(rewrite_affiliate_url(url), url)
        self.assertEqual(rewrite_affiliate_url(url, associate_tag=""), url)

    def test_leaves_non_amazon_and_lookalike_hosts_untouched(self) -> None:
        urls = [
            "https://www.homedepot.com/p/Milwaukee-M18/343992114",
            "https://slickdeals.net/f/20346027?utm_source=rss",
            "https://notamazon.com/dp/B09Y9G25CY",
            "https://amazon.com.evil.test/dp/B09Y9G25CY",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(rewrite_affiliate_url(url, associate_tag="tooldrop-20"), url)


if __name__ == "__main__":
    unittest.main()
