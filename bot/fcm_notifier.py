"""Optional Firebase Cloud Messaging HTTP v1 topic notifications for hot deals."""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Callable

from bot.models import ParsedDeal

LOGGER = logging.getLogger(__name__)

_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_MAX_PUSHES_PER_RUN = 5


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_rs256_with_openssl(signing_input: bytes, private_key_pem: str) -> bytes:
    """Sign a JWT payload using OpenSSL CLI so zero third-party deps are needed."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=True) as key_file:
        key_file.write(private_key_pem)
        key_file.flush()
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_file.name],
            input=signing_input,
            capture_output=True,
            check=True,
        )
        return proc.stdout


def _fetch_access_token(service_account: dict[str, str]) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    claims = _b64url(
        json.dumps(
            {
                "iss": service_account["client_email"],
                "scope": _FCM_SCOPE,
                "aud": _OAUTH_TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    signature = _b64url(_sign_rs256_with_openssl(signing_input, service_account["private_key"]))
    jwt_assertion = f"{header}.{claims}.{signature}"

    body = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_assertion,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        _OAUTH_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return str(payload["access_token"])


def _default_post_fcm(project_id: str, access_token: str, message_body: dict[str, object]) -> None:
    endpoint = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    data = json.dumps(message_body).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10):
        return


def notify_hot_deals(
    deals: list[ParsedDeal],
    previous_ids: set[str],
    service_account_json: str | None = None,
    token_provider: Callable[[dict[str, str]], str] = _fetch_access_token,
    message_sender: Callable[[str, str, dict[str, object]], None] = _default_post_fcm,
) -> int:
    """Send FCM topic pushes (`brand_<lowercase>`) for newly detected hot deals.

    Returns 0 cleanly when FIREBASE_SERVICE_ACCOUNT_JSON is unset or empty.
    """
    raw_sa = (
        service_account_json
        if service_account_json is not None
        else os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    ).strip()
    if not raw_sa:
        return 0

    new_hot_deals = [d for d in deals if d.isHot and d.id not in previous_ids][:_MAX_PUSHES_PER_RUN]
    if not new_hot_deals:
        return 0

    try:
        service_account = json.loads(raw_sa)
        project_id = str(service_account["project_id"])
        access_token = token_provider(service_account)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Skipping FCM notifications due to invalid config/auth: %s", exc)
        return 0

    sent = 0
    for deal in new_hot_deals:
        topic = f"brand_{deal.brand.value.lower()}"
        price_prefix = f"${deal.currentPrice:g} · " if deal.currentPrice is not None else ""
        payload: dict[str, object] = {
            "message": {
                "topic": topic,
                "notification": {
                    "title": f"Hot {deal.brand.value.replace('_', ' ').title()} Deal",
                    "body": f"{price_prefix}{deal.title}"[:180],
                },
                "data": {
                    "dealId": deal.id,
                    "brand": deal.brand.value,
                    "dealUrl": deal.dealUrl,
                },
            }
        }
        try:
            message_sender(project_id, access_token, payload)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed sending FCM push for %s: %s", deal.id, exc)
    return sent
