#!/usr/bin/env python3
"""
HTTP-over-Reticulum API request (rspace TASK-HIGH.15, option a).

Speaks the single-frame JSON wire protocol of the rspace api proxy
(`docker/rserver/proxy.py`). One request frame out, one response frame
back, over an already-established RNS Link to the `rspace.api`
destination.

    REQUEST FRAME:
        {"v": 1, "method": "POST", "path": "/api/zk-pki/verify",
         "headers": {"content-type": "application/json", ...},
         "body_b64": "<base64>" | null}

    RESPONSE FRAME:
        {"v": 1, "status": 200,
         "headers": {"content-type": "application/json", ...},
         "body_b64": "<base64>" | null}

The proxy ships small frames as RNS Packets and larger ones as RNS
Resources; we register callbacks for both so either arrives at the
same handler. Mirror of `shared/transport/rweb-api-wire.ts` on the
rspace side — keep the two in lockstep.
"""

import base64
import json
import threading
from typing import Dict, Tuple

import RNS

# Match proxy.py's WIRE_VERSION and its <256-byte packet/resource split.
WIRE_VERSION = 1
PACKET_MAX_BYTES = 256
RESPONSE_TIMEOUT = 300  # seconds


def encode_request_frame(
    method: str, path: str, headers: Dict[str, str] | None, body: bytes | None
) -> bytes:
    frame = {
        "v": WIRE_VERSION,
        "method": method.upper(),
        "path": path,
        "headers": {str(k).lower(): str(v) for k, v in (headers or {}).items()},
        "body_b64": base64.b64encode(body).decode("ascii") if body else None,
    }
    return json.dumps(frame, separators=(",", ":")).encode("utf-8")


def decode_response_frame(payload: bytes) -> Tuple[int, Dict[str, str], bytes]:
    obj = json.loads(payload.decode("utf-8"))
    if not isinstance(obj, dict) or obj.get("v") != WIRE_VERSION:
        raise ValueError("malformed response frame: bad version")
    if not isinstance(obj.get("status"), int):
        raise ValueError("malformed response frame: missing status")
    status = obj["status"]
    headers = {str(k).lower(): str(v) for k, v in (obj.get("headers") or {}).items()}
    body_b64 = obj.get("body_b64")
    body = base64.b64decode(body_b64) if body_b64 else b""
    return status, headers, body


def fetch_api(
    link: "RNS.Link",
    method: str,
    path: str,
    headers: Dict[str, str] | None,
    body: bytes | None,
) -> Tuple[int, Dict[str, str], bytes]:
    """
    Send one API request frame over an established Link and return the
    decoded (status, headers, body). Raises on transport timeout or a
    malformed/failed response — note that an upstream HTTP error (e.g.
    500) round-trips as a normal response frame, NOT an exception.
    """
    response_event = threading.Event()
    state: Dict[str, object] = {"payload": None, "error": None}

    def _capture(payload: bytes) -> None:
        if state["payload"] is None and state["error"] is None:
            state["payload"] = payload
        response_event.set()

    def _on_packet(message: bytes, packet: "RNS.Packet") -> None:
        _capture(message)

    def _on_resource_concluded(resource: "RNS.Resource") -> None:
        try:
            if resource.status == RNS.Resource.COMPLETE:
                _capture(resource.data.read())
            else:
                state["error"] = f"resource transfer failed: {resource.status}"
                response_event.set()
        except Exception as e:  # noqa: BLE001
            state["error"] = str(e)
            response_event.set()

    link.set_packet_callback(_on_packet)
    link.set_resource_strategy(RNS.Link.ACCEPT_ALL)
    link.set_resource_concluded_callback(_on_resource_concluded)

    frame = encode_request_frame(method, path, headers, body)
    if len(frame) < PACKET_MAX_BYTES:
        RNS.Packet(link, frame).send()
    else:
        RNS.Resource(frame, link)

    if not response_event.wait(timeout=RESPONSE_TIMEOUT):
        raise ConnectionError(
            f"no API response within {RESPONSE_TIMEOUT}s for {method} {path}"
        )
    if state["error"]:
        raise ConnectionError(str(state["error"]))
    return decode_response_frame(state["payload"])  # type: ignore[arg-type]
