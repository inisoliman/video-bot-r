import base64
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, Optional

import config

logger = logging.getLogger(__name__)


def _urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _get_signing_secret() -> str:
    secret = os.environ.get("LINK_SIGNING_SECRET")
    if secret:
        return secret
    if getattr(config, "BOT_TOKEN", None):
        return config.BOT_TOKEN
    logger.warning("LINK_SIGNING_SECRET not set, falling back to BOT_TOKEN for token signing.")
    return "default-link-secret"


def _sign_payload(payload: bytes) -> str:
    secret = _get_signing_secret().encode("utf-8")
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    return _urlsafe_b64encode(signature)


def encode_stream_token(video_id: int) -> str:
    payload = json.dumps({"video_id": int(video_id)}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    token_payload = _urlsafe_b64encode(payload)
    token_signature = _sign_payload(payload)
    return f"{token_payload}.{token_signature}"


def decode_stream_token(token: str) -> Dict[str, Any]:
    try:
        payload_b64, signature = token.split(".")
    except ValueError:
        raise ValueError("Invalid stream token format")

    payload_bytes = _urlsafe_b64decode(payload_b64)
    expected_signature = _sign_payload(payload_bytes)

    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("Invalid stream token signature")

    data = json.loads(payload_bytes.decode("utf-8"))
    if "video_id" not in data:
        raise ValueError("Invalid stream token payload")

    data["video_id"] = int(data["video_id"])
    return data


def _normalize_url(base_url: Optional[str]) -> Optional[str]:
    if not base_url:
        return None
    return base_url.rstrip("/")


def build_hostinger_watch_url(token: str, mode: str = "watch") -> Optional[str]:
    base_url = _normalize_url(config.PUBLIC_STREAM_PAGE_URL)
    if not base_url:
        return None
    return f"{base_url}?id={token}&mode={mode}"


def build_hostinger_download_url(token: str) -> Optional[str]:
    return build_hostinger_watch_url(token, mode="download")


def build_render_stream_url(token: str) -> Optional[str]:
    app_url = _normalize_url(config.APP_URL)
    if not app_url:
        return None
    return f"{app_url}/stream/{token}"


def build_render_download_url(token: str) -> Optional[str]:
    app_url = _normalize_url(config.APP_URL)
    if not app_url:
        return None
    return f"{app_url}/download/{token}"


def build_render_watch_url(token: str) -> Optional[str]:
    app_url = _normalize_url(config.APP_URL)
    if not app_url:
        return None
    return f"{app_url}/watch/{token}"
