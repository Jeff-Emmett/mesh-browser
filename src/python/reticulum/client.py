#!/usr/bin/env python3
"""
ReticulumClient - Thin coordinator for Reticulum operations

Provides a simple interface that delegates to focused utility modules.

Two delivery paths:
- Static content (`rserver.web` destination) — `fetch_page`, GET-only,
  the original rweb behaviour.
- rspace API surface (`rspace.api` destination) — `fetch_api`, any
  method, JSON-wire framed (rspace TASK-HIGH.15, option a). The page
  origin stays `rweb://<static-hash>/`; the renderer's
  `fetch('/api/...')` is transparently rerouted to the api
  destination advertised by the page's `<link rel="rspace-api">` hint.

The api destination is PINNED to the static destination's identity: we
only route `/api/*` to it after confirming the same Reticulum identity
announced both. That stops a tampered bundle from pointing auth/sync
traffic at an attacker-controlled destination.
"""

import base64
import threading
from typing import Any, Dict, Optional

import RNS

from .url import parse_url
from .link import establish_link, recall_identity
from .fetch import fetch
from .api_fetch import fetch_api
from .discovery import parse_api_hint
from .response import parse_response
from .status import get_status

# Reticulum context for the rspace api destination — must match
# PROXY_APP_NAME / PROXY_ASPECT in docker/rserver/proxy.py.
API_APP_NAME = "rspace"
API_ASPECT = "api"


class ReticulumClient:
    """Coordinates Reticulum networking operations"""

    def __init__(self):
        """Initialize Reticulum networking"""
        self.reticulum = RNS.Reticulum()
        # static-dest-hex -> api-dest-hex (str) once discovered, or
        # None if the page carries no valid hint. Guards repeat parsing.
        self._api_hint: Dict[str, Optional[str]] = {}
        # static-dest-hex -> bool: result of the same-identity pin.
        self._api_trusted: Dict[str, bool] = {}
        self._lock = threading.Lock()

    # ── Public router ────────────────────────────────────────────────

    def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        Route a request to the right Reticulum destination based on its
        path. `/api/*` goes through the pinned api proxy; everything
        else is static content served by rserver.
        """
        dest_hash, path = parse_url(url)
        if path.startswith("/api/"):
            return self.fetch_api(dest_hash, path, method, headers, body)
        return self._fetch_static(dest_hash, path)

    def fetch_page(self, url: str) -> Dict[str, Any]:
        """Fetch static content from a Reticulum destination (GET)."""
        dest_hash, path = parse_url(url)
        return self._fetch_static(dest_hash, path)

    def get_status(self) -> Dict[str, Any]:
        """Get current Reticulum status and system information"""
        return get_status()

    # ── Static path ──────────────────────────────────────────────────

    def _fetch_static(self, dest_hash: bytes, path: str) -> Dict[str, Any]:
        link = establish_link(dest_hash, "rserver", "web")
        try:
            raw_content = fetch(link, dest_hash, path)
        finally:
            link.teardown()

        result = parse_response(raw_content, path)
        # Opportunistically learn the api-destination hint from any HTML
        # the page serves (typically index.html), so a later
        # fetch('/api/...') already knows where to route. Cover the root
        # path explicitly — rserver may not set a content-type header and
        # mimetypes can't guess one from "/".
        ctype = (result.get("content_type") or "").lower()
        if "html" in ctype or path == "/" or path.endswith(".html"):
            self._remember_hint(dest_hash, raw_content)
        return result

    # ── API path ─────────────────────────────────────────────────────

    def fetch_api(
        self,
        static_hash: bytes,
        path: str,
        method: str,
        headers: Optional[Dict[str, str]],
        body: Optional[bytes],
    ) -> Dict[str, Any]:
        """
        Tunnel an `/api/*` request to the page's pinned api destination
        and return a structured response (status / headers / base64
        body). Raises ConnectionError if no trusted api destination is
        available — the caller surfaces that to the renderer.
        """
        api_hex = self._ensure_api_hint(static_hash)
        if not api_hex:
            raise ConnectionError(
                "this rweb site does not advertise an API destination "
                "(no <link rel=\"rspace-api\"> hint); /api/* is unavailable "
                "over Reticulum"
            )

        if not self._verify_same_identity(static_hash, api_hex):
            raise ConnectionError(
                "refusing to route /api/* — the advertised API destination "
                f"({api_hex}) is not owned by the same Reticulum identity as "
                "the site that served this page. Possible tampered bundle."
            )

        api_hash = bytes.fromhex(api_hex)
        link = establish_link(api_hash, API_APP_NAME, API_ASPECT)
        try:
            status, resp_headers, resp_body = fetch_api(
                link, method, path, headers, body
            )
        finally:
            link.teardown()

        return {
            "status_code": status,
            "headers": resp_headers,
            "content": base64.b64encode(resp_body).decode("ascii") if resp_body else "",
            "content_type": resp_headers.get("content-type", "application/octet-stream"),
            "encoding": "base64",
        }

    # ── Discovery + trust ────────────────────────────────────────────

    def _remember_hint(self, static_hash: bytes, html: bytes) -> None:
        # Positive-only: only record a hint we actually found, so a
        # later non-HTML asset fetch can't clobber a good hint with None.
        api_hex = parse_api_hint(html)
        if api_hex:
            with self._lock:
                self._api_hint[static_hash.hex()] = api_hex

    def _ensure_api_hint(self, static_hash: bytes) -> Optional[str]:
        key = static_hash.hex()
        with self._lock:
            if key in self._api_hint:
                return self._api_hint[key]
        # Not cached yet (an /api/* call beat the page load) — fetch the
        # site root to discover the hint, then re-check the cache.
        try:
            self._fetch_static(static_hash, "/")
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            # Tried the root; if still no hint, cache None so we don't
            # re-fetch "/" on every subsequent /api/* call.
            if key not in self._api_hint:
                self._api_hint[key] = None
            return self._api_hint[key]

    def _verify_same_identity(self, static_hash: bytes, api_hex: str) -> bool:
        """
        Pin: the api destination must be announced by the same Reticulum
        identity as the static destination. Cached per static dest.
        """
        key = static_hash.hex()
        with self._lock:
            if key in self._api_trusted:
                return self._api_trusted[key]

        static_id = recall_identity(static_hash)
        api_id = recall_identity(bytes.fromhex(api_hex))
        trusted = (
            static_id is not None
            and api_id is not None
            and static_id.hash == api_id.hash
        )
        if not trusted:
            RNS.log(
                f"[rweb] api-dest pin FAILED for site {key[:16]}… "
                f"(api={api_hex[:16]}…): "
                f"static_id={'?' if static_id is None else static_id.hash.hex()[:16]} "
                f"api_id={'?' if api_id is None else api_id.hash.hex()[:16]}",
                RNS.LOG_WARNING,
            )
        with self._lock:
            self._api_trusted[key] = trusted
        return trusted
