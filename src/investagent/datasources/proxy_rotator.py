"""Clash proxy rotator for AkShare rate limit bypass.

Rotates through Clash proxy nodes via the mihomo unix socket API.
Each rotation changes the exit IP, distributing rate limits across nodes.

Usage:
    rotator = ClashRotator()
    rotator.patch_requests()  # monkey-patch requests to use Clash proxy
    # Now all requests.get/post calls go through Clash with rotating IPs
    rotator.rotate()  # switch to next node

Health check:
    healthy = rotator.health_check()  # test all nodes, keep only alive ones
"""

from __future__ import annotations

import concurrent.futures
import http.client
import json
import logging
import os
import socket
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_SOCKET = os.getenv("CLASH_SOCKET", "/var/tmp/verge/verge-mihomo.sock")
_DEFAULT_PROXY = os.getenv("CLASH_PROXY", "http://127.0.0.1:7890")
_DEFAULT_GROUP = os.getenv("CLASH_GROUP", "♻️ 手动选择节点")

# Skip info nodes (not real proxies)
_SKIP_KEYWORDS = ("网址", "流量", "到期", "重置", "自动选择", "故障转移",
                   "剩余", "套餐", "DIRECT", "REJECT", "PASS", "COMPATIBLE")

# Default: only use HK nodes for accessing CN financial data sources
_DEFAULT_NODE_PATTERNS = os.getenv(
    "CLASH_NODE_PATTERNS",
    "Lite-香港,pro-香港",
).split(",")

# Domains eligible for proxy fallback (direct-first, proxy on failure).
_PROXY_DOMAINS = (
    "push2his.eastmoney.com",  # 东财历史行情 (直连不通，需代理)
    "push2.eastmoney.com",     # 东财实时行情 (直连不通，需代理)
    "10jqka.com.cn",           # 同花顺
    "sina.com.cn",             # 新浪
    "legulegu.com",            # 乐股 (Shenwan)
    "csindex.com.cn",          # 中证指数
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
        node_patterns: list[str] | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._proxy_url = proxy_url
        self._group = group
        self._node_patterns = node_patterns or _DEFAULT_NODE_PATTERNS
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
        # Filter out info/system nodes
        candidates = [
            n for n in all_nodes
            if not any(kw in n for kw in _SKIP_KEYWORDS)
        ]
        # If node_patterns specified, only keep matching nodes
        if self._node_patterns and any(p.strip() for p in self._node_patterns):
            patterns = [p.strip() for p in self._node_patterns if p.strip()]
            candidates = [
                n for n in candidates
                if any(p in n for p in patterns)
            ]
        self._nodes = candidates
        logger.info(
            "Clash rotator: %d nodes matched patterns %s (from %d total)",
            len(self._nodes), self._node_patterns, len(all_nodes),
        )

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

    def _test_node(self, node: str, test_url: str, timeout: float) -> tuple[str, bool, float]:
        """Switch to *node*, make a test request, return (node, ok, latency_ms)."""
        encoded_group = urllib.request.quote(self._group)
        self._api("PUT", f"/proxies/{encoded_group}", {"name": node})
        # Small settle time for the proxy switch
        time.sleep(0.3)
        t0 = time.time()
        try:
            import requests as _req
            resp = _req.get(
                test_url,
                proxies={"http": self._proxy_url, "https": self._proxy_url},
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            latency = (time.time() - t0) * 1000
            ok = resp.status_code < 400
            return node, ok, latency
        except Exception:
            latency = (time.time() - t0) * 1000
            return node, False, latency

    def health_check(
        self,
        test_url: str = "https://basic.10jqka.com.cn/api/stock/finance/600519_benefit.json",
        timeout: float = 8,
    ) -> list[dict[str, Any]]:
        """Test all proxy nodes, keep only healthy ones.

        Switches each node in, makes a request to test_url through the proxy,
        and records pass/fail + latency.  After the check, self._nodes is
        filtered to only healthy nodes, sorted by latency (fastest first).

        Returns a report list: [{"node": ..., "ok": bool, "latency_ms": float}, ...]
        """
        if not self._nodes:
            logger.warning("No nodes to health-check")
            return []

        logger.info(
            "Health-checking %d proxy nodes against %s (timeout=%gs)...",
            len(self._nodes), test_url, timeout,
        )

        # Test nodes sequentially (each test switches the global proxy)
        report: list[dict[str, Any]] = []
        for node in self._nodes:
            name, ok, latency = self._test_node(node, test_url, timeout)
            status = "OK" if ok else "FAIL"
            logger.info("  %-40s %s  %.0fms", name, status, latency)
            report.append({"node": name, "ok": ok, "latency_ms": latency})

        healthy = [r["node"] for r in report if r["ok"]]
        dead = [r["node"] for r in report if not r["ok"]]

        # Sort healthy nodes by latency (fastest first)
        latency_map = {r["node"]: r["latency_ms"] for r in report}
        healthy.sort(key=lambda n: latency_map[n])

        self._nodes = healthy
        self._index = 0

        logger.info(
            "Health check done: %d healthy, %d dead out of %d total",
            len(healthy), len(dead), len(report),
        )
        if dead:
            logger.info("Dead nodes: %s", ", ".join(dead[:10]))

        return report

    def patch_requests(self) -> None:
        """Monkey-patch requests.Session.send: direct by default, proxy on rate limit.

        Strategy: keep-alive direct connections for speed. Only switch to proxy
        when a request fails with connection/SSL errors (likely rate limited).
        This gives direct-connection speed (~1.5/s) with proxy fallback safety.
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
        rotator = self

        # Track per-domain failure state
        _using_proxy: set[str] = set()  # domains currently routed through proxy
        _direct_failures: dict[str, int] = {}  # consecutive direct failures per domain
        _FAILURE_THRESHOLD = 1  # switch to proxy after 1st direct failure (fast for always-blocked hosts)
        _RECOVERY_INTERVAL = 300  # try direct again after 5 minutes
        _proxy_activated_at: dict[str, float] = {}

        def _patched_send(self_session: Any, request: Any, **kwargs: Any) -> Any:
            host = urlparse(request.url).hostname or ""
            is_proxy_domain = any(domain in host for domain in _PROXY_DOMAINS)

            if not is_proxy_domain:
                return _original_send(self_session, request, **kwargs)

            # Check if we should try switching back to direct
            if host in _using_proxy:
                activated = _proxy_activated_at.get(host, 0)
                if time.time() - activated > _RECOVERY_INTERVAL:
                    _using_proxy.discard(host)
                    _direct_failures[host] = 0
                    logger.info("Proxy recovery: trying direct for %s", host)

            # Route through proxy if activated for this domain
            if host in _using_proxy:
                kwargs["proxies"] = {
                    "http": proxy_url,
                    "https": proxy_url,
                }
                try:
                    return _original_send(self_session, request, **kwargs)
                except Exception:
                    # Proxy also failed — rotate node and retry
                    rotator.rotate()
                    raise

            # Default: direct connection (fast, keep-alive)
            try:
                resp = _original_send(self_session, request, **kwargs)
                _direct_failures[host] = 0  # reset on success
                return resp
            except Exception as e:
                _direct_failures[host] = _direct_failures.get(host, 0) + 1
                if _direct_failures[host] >= _FAILURE_THRESHOLD:
                    _using_proxy.add(host)
                    _proxy_activated_at[host] = time.time()
                    rotator.rotate()
                    logger.warning(
                        "Direct failed %dx for %s — switching to proxy (node: %s)",
                        _direct_failures[host], host,
                        rotator._nodes[rotator._index % len(rotator._nodes)] if rotator._nodes else "?",
                    )
                raise

        requests.Session.send = _patched_send  # type: ignore[assignment]
        self._patched = True
        logger.info(
            "Patched requests.Session.send: direct-first, proxy on rate limit — domains: %s",
            ", ".join(_PROXY_DOMAINS),
        )
