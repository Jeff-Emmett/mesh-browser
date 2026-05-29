#!/usr/bin/env python3
"""
rweb API-destination discovery (rspace TASK-HIGH.15, option a).

An rspace site served over Reticulum ships its static bundle from one
destination (`rserver.web`) and tunnels its `/api/*` surface from a
second destination (`rspace.api`). The static `index.html` advertises
the second destination with a discovery hint:

    <link rel="rspace-api" href="rweb-api://<32-hex-dest-hash>/" />

mesh-browser parses that hint so a shell-side `fetch('/api/...')` —
which the renderer resolves against the page origin `rweb://<static>/`
— can be transparently rerouted to the api destination. The shell
itself is unchanged.

If the hint is absent (plain rserver site) or still carries the
build-time sentinel (`__RWEB_API_DEST_HASH__`, which is not valid
hex), `parse_api_hint` returns None and the caller falls back to
serving `/api/*` from the static destination (which 404s — the same
behaviour as before this feature existed).
"""

import re

# RNS SINGLE destination hashes are exactly 32 hex chars (16 bytes).
_HEX32 = r"([0-9a-fA-F]{32})"

# Match both attribute orderings (rel-before-href and href-before-rel)
# and single/double quotes. A trailing slash after the hash is optional.
_HINT_PATTERNS = [
    re.compile(
        rb'<link\b[^>]*\brel=["\']rspace-api["\'][^>]*\bhref=["\']rweb-api://'
        + _HEX32.encode()
        + rb'/?["\']',
        re.IGNORECASE,
    ),
    re.compile(
        rb'<link\b[^>]*\bhref=["\']rweb-api://'
        + _HEX32.encode()
        + rb'/?["\'][^>]*\brel=["\']rspace-api["\']',
        re.IGNORECASE,
    ),
]


def parse_api_hint(html: bytes) -> str | None:
    """
    Return the api destination hash (lowercase 32-hex str) from the
    <link rel="rspace-api"> hint in an HTML document, or None if there
    is no valid hint.
    """
    if not html:
        return None
    for pattern in _HINT_PATTERNS:
        match = pattern.search(html)
        if match:
            return match.group(1).decode("ascii").lower()
    return None
