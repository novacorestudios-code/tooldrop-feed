"""Amazon Associates affiliate tag rewriting helper for ToolDrop."""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_AMAZON_HOSTS = frozenset({"amazon.com", "www.amazon.com"})


def rewrite_affiliate_url(url: str, associate_tag: str | None = None) -> str:
    """Attach or replace the Amazon Associates tag on US Amazon URLs.

    Non-Amazon URLs or empty/unset associate tags leave the URL unchanged.
    """
    tag = (associate_tag if associate_tag is not None else os.environ.get("AMAZON_ASSOCIATE_TAG", "")).strip()
    if not tag or not url:
        return url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _AMAZON_HOSTS:
        return url

    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() != "tag"]
    params.append(("tag", tag))
    updated_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=updated_query))
