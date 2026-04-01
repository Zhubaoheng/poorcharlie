"""Clash proxy rotator for AkShare rate limit bypass.

Rotates through Clash proxy nodes via the mihomo unix socket API.
Each rotation changes the exit IP, distributing rate limits across nodes.

Usage:
    rotator = ClashRotator()
    rotator.patch_requests()  # monkey-patch requests to use Clash proxy
    # Now all requests.get/post calls go through Clash with rotating IPs
    rotator.rotate()  # switch to next node
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_SOCKET = os.getenv("CLASH_SOCKET", "/var/tmp/verge/verge-mihomo.sock")
_DEFAULT_PROXY = os.getenv("CLASH_PROXY", "http://127.0.0.1:7890")
_DEFAULT_GROUP = os.getenv("CLASH_GROUP", "龙猫云 - TotoroCloud")

# Skip info nodes (not real proxies)
_SKIP_KEYWORDS = ("网址", "流量", "到期", "重置", "自动选择", "故障转移")

# Only route these domains through proxy (AkShare data sources)
_PROXY_DOMAINS = (
    "eastmoney.com",
    "10jqka.com.cn",    # 同花顺
    "sina.com.cn",      # 新浪
    "legulegu.com",     # 乐股 (Shenwan)
    "csindex.com.cn",   # 中证指数
)


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._socket_path)


class ClashRotator:
    """Rotate Clash proxy nodes to distribute rate limits."""

    def __init__(
        self,
        socket_path: str = _DEFAULT_SOCKET,
        proxy_url: str = _DEFAULT_PROXY,
        group: str = _DEFAULT_GROUP,
    ) -> None:
        self._socket_path = socket_path
        self._proxy_url = proxy_url
        self._group = group
        self._nodes: list[str] = []
        self._index = 0
        self._patched = False
        self._load_nodes()

    def _api(self, method: str, path: str, body: dict | None = None) -> dict | None:
        try:
            conn = _UnixHTTPConnection(self._socket_path)
            headers = {"Content-Type": "application/json"} if body else {}
            data = json.dumps(body).encode() if body else None
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            if resp.status == 204:
                return None
            return json.loads(resp.read())
        except Exception:
            logger.debug("Clash API failed", exc_info=True)
            return None

    def _load_nodes(self) -> None:
        encoded = urllib.request.quote(self._group)
        data = self._api("GET", f"/proxies/{encoded}")
        if not data:
            logger.warning("Could not load Clash proxy nodes (socket: %s)", self._socket_path)
            return
        all_nodes = data.get("all", [])
        self._nodes = [
            n for n in all_nodes
            if not any(kw in n for kw in _SKIP_KEYWORDS)
        ]
        logger.info("Clash rotator: %d proxy nodes available", len(self._nodes))

    @property
    def available(self) -> bool:
        return len(self._nodes) > 0

    def rotate(self) -> str | None:
        """Switch to next proxy node. Returns node name or None if unavailable."""
        if not self._nodes:
            return None
        node = self._nodes[self._index % len(self._nodes)]
        self._index += 1
        encoded = urllib.request.quote(self._group)
        self._api("PUT", f"/proxies/{encoded}", {"name": node})
        logger.debug("Rotated to proxy node: %s", node)
        return node

    def patch_requests(self) -> None:
        """Monkey-patch requests.Session.send to selectively proxy AkShare domains.

        Only routes requests to financial data domains (eastmoney, 同花顺, sina, etc.)
        through the Clash proxy. All other requests (MiniMax API, yfinance, etc.)
        go direct — no added latency or dependency on Clash.
        """
        if self._patched:
            return
        if not self.available:
            logger.info("No proxy nodes available, skipping patch")
            return

        import requests
        from urllib.parse import urlparse

        _original_send = requests.Session.send
        proxy_url = self._proxy_url

        def _patched_send(self_session: Any, request: Any, **kwargs: Any) -> Any:
            # Check if this request should go through proxy
            host = urlparse(request.url).hostname or ""
            if any(domain in host for domain in _PROXY_DOMAINS):
                kwargs.setdefault("proxies", {
                    "http": proxy_url,
                    "https": proxy_url,
                })
            return _original_send(self_session, request, **kwargs)

        requests.Session.send = _patched_send  # type: ignore[assignment]
        self._patched = True
        logger.info(
            "Patched requests.Session.send for selective proxy (%s) — domains: %s",
            self._proxy_url, ", ".join(_PROXY_DOMAINS),
        )
