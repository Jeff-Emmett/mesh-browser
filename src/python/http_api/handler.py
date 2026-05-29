#!/usr/bin/env python3
"""
HTTP Request Handler for MeshBrowser Backend

Handles HTTP requests to the /proxy/reticulum endpoint and communicates
with the Reticulum network through the ReticulumHandler.
"""

import base64
import json
import sys
from http.server import BaseHTTPRequestHandler
from typing import Dict, Any

import reticulum as Reticulum



class HTTP_API_Handler(BaseHTTPRequestHandler):
    """HTTP request handler for Reticulum proxy requests"""

    def __init__(self, reticulum_client, *args, **kwargs):
        # Use shared ReticulumClient instance (created in main thread)
        self.reticulum_client = reticulum_client
        super().__init__(*args, **kwargs)

    def do_GET(self):
        """Handle GET requests"""
        try:
            if self.path == '/api/status':
                self._handle_status_request()
            else:
                self._send_error(404, "Not Found")
        except Exception as e:
            self._send_error(500, f"Internal Server Error: {str(e)}")

    def do_POST(self):
        """Handle POST requests to /proxy/reticulum endpoint"""
        try:
            # Only handle /proxy/reticulum endpoint
            if self.path == '/proxy/reticulum':
                self._handle_reticulum_proxy()
            else:
                self._send_error(404, "Not Found")
        except Exception as e:
            self._send_error(500, f"Internal Server Error: {str(e)}")

    def _handle_status_request(self):
        """Handle status requests"""
        try:
            status_data = self.reticulum_client.get_status()
            status_json = json.dumps(status_data)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(status_json)))
            self.end_headers()

            self.wfile.write(status_json.encode('utf-8'))
        except Exception as e:
            self._send_error(500, f"Failed to get status: {str(e)}")

    def _handle_reticulum_proxy(self):
        """Handle proxy requests to Reticulum network"""
        # Read request body
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self._send_error(400, "Request body required")
            return

        try:
            request_body = self.rfile.read(content_length).decode('utf-8')
            request_data = json.loads(request_body)
        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON")
            return

        # Extract and validate required fields
        url = request_data.get('url')
        if not url:
            self._send_error(400, "Missing 'url' field")
            return

        method = request_data.get('method', 'GET').upper()

        # `/api/*` paths tunnel through the rspace api proxy and accept
        # any method + headers + body. Static rserver content is GET-only
        # (HTTP-like request/response, no body). Let the client route by
        # path; only reject non-GET when there's no API path to carry it.
        is_api = '/api/' in url[url.find('/'):] if '/' in url else False
        if method != 'GET' and not is_api:
            self._send_error(501, f"Method {method} not supported for static rweb content")
            return

        headers = request_data.get('headers') or {}
        body_b64 = request_data.get('body_b64')
        try:
            body = base64.b64decode(body_b64) if body_b64 else None
        except Exception:
            self._send_error(400, "Invalid base64 body")
            return

        try:
            # Route via the client (static vs pinned api proxy).
            result = self.reticulum_client.fetch(url, method, headers, body)
            self._send_reticulum_response(result)
        except (RuntimeError, ValueError, ConnectionError, TimeoutError) as e:
            self._send_error(502, str(e))
        except Exception as e:
            self._send_error(500, f'Unexpected error: {str(e)}')

    def _send_error(self, code: int, message: str):
        """Send error response"""
        error_data = {'error': message}
        error_json = json.dumps(error_data)

        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('X-Backend-Error', 'true')
        self.send_header('Content-Length', str(len(error_json)))
        self.end_headers()

        self.wfile.write(error_json.encode('utf-8'))

    # Hop-by-hop / framing headers we re-derive ourselves rather than
    # echo from the upstream API response.
    _SKIP_HEADERS = {
        'content-length', 'transfer-encoding', 'connection',
        'keep-alive', 'upgrade',
    }

    def _send_reticulum_response(self, data: Dict[str, Any]):
        """Send Reticulum content as native HTTP response"""
        # Extract response data
        content_b64 = data.get('content', '')
        content_type = data.get('content_type', 'text/html')
        status_code = data.get('status_code', 200)
        passthrough_headers = data.get('headers')  # present for /api/* responses

        # Decode base64 content to raw bytes
        try:
            content_bytes = base64.b64decode(content_b64) if content_b64 else b''
        except Exception:
            self._send_error(500, "Invalid base64 content")
            return

        self.send_response(status_code)
        if passthrough_headers:
            # API path — echo upstream headers verbatim (auth, content-type,
            # set-cookie, …) except framing ones we recompute below.
            for name, value in passthrough_headers.items():
                if name.lower() not in self._SKIP_HEADERS:
                    self.send_header(name, value)
        else:
            # Static path — only the guessed content type is meaningful.
            self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content_bytes)))
        self.end_headers()

        # Send raw content bytes
        self.wfile.write(content_bytes)

    def log_message(self, format, *args):
        """Override to send logs to stderr instead of stdout"""
        print(f"HTTP: {format % args}", file=sys.stderr, flush=True)