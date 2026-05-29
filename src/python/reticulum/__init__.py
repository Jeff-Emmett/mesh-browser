"""
Reticulum Network Package

Provides Reticulum mesh network communication for MeshBrowser.
Handles page fetching, status queries, and network operations.

Structure:
- client.py: Main coordinator interface + static/api routing
- url.py: URL parsing utilities
- link.py: RNS link establishment + identity recall (transport layer)
- fetch.py: Static content fetching (application layer)
- api_fetch.py: HTTP-over-Reticulum JSON-wire API requests (rspace api proxy)
- discovery.py: <link rel="rspace-api"> api-destination discovery
- response.py: HTTP response parsing
- status.py: Status information gathering
"""

from .client import ReticulumClient

# Provide shorter alias for cleaner usage
Client = ReticulumClient

__all__ = ['ReticulumClient', 'Client']