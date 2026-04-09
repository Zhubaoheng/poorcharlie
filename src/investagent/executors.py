"""Shared executors for async workloads — split by type.

- subprocess_extract_pdf / subprocess_extract_sections:
  Run CPU-intensive PDF work in a separate process (true multi-core).
- io_pool: ThreadPoolExecutor for I/O-bound blocking work.

The subprocess approach avoids both GIL contention (threads) and
fork-safety issues (ProcessPoolExecutor on macOS). Each call spawns
a fresh Python process via asyncio.create_subprocess_exec.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# I/O thread pool (HTTP downloads, API calls)
# ---------------------------------------------------------------------------

_io_pool: ThreadPoolExecutor | None = None


def io_pool() -> ThreadPoolExecutor:
    """Thread pool for I/O-bound work (HTTP downloads, API calls)."""
    global _io_pool
    if _io_pool is None:
        _io_pool = ThreadPoolExecutor(
            max_workers=32,
            thread_name_prefix="io",
        )
        atexit.register(_io_pool.shutdown, wait=False)
    return _io_pool


# ---------------------------------------------------------------------------
# CPU-intensive work via subprocess (bypass GIL, true multi-core)
# ---------------------------------------------------------------------------

_WORKER_MODULE = "investagent.datasources.pdf_extract_worker"
# Limit concurrent CPU subprocesses — defaults to cpu_count, can be
# raised via set_cpu_concurrency() to match pipeline concurrency.
_CPU_SEM: asyncio.Semaphore | None = None


def set_cpu_concurrency(n: int) -> None:
    """Set CPU subprocess concurrency (call before any extraction)."""
    global _CPU_SEM
    _CPU_SEM = asyncio.Semaphore(n)


def _get_cpu_sem() -> asyncio.Semaphore:
    global _CPU_SEM
    if _CPU_SEM is None:
        _CPU_SEM = asyncio.Semaphore(os.cpu_count() or 4)
    return _CPU_SEM


async def subprocess_extract_pdf(raw_content: bytes) -> str:
    """Extract markdown from PDF bytes in a subprocess (true parallel)."""
    async with _get_cpu_sem():
        header = json.dumps({
            "action": "extract_markdown",
            "data_len": len(raw_content),
        }).encode() + b"\n"

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", _WORKER_MODULE,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(header + raw_content)

        if proc.returncode != 0:
            logger.warning("PDF extract subprocess failed: %s", stderr.decode()[:500])
            return ""

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            logger.warning("PDF extract subprocess invalid JSON: %s", stdout[:500])
            return ""

        if "error" in result:
            logger.warning("PDF extract subprocess error: %s", result["error"])
            return ""

        return result.get("text", "")


async def subprocess_extract_sections(text: str, market: str) -> dict[str, str]:
    """Extract sections from markdown text in a subprocess (true parallel)."""
    async with _get_cpu_sem():
        header = json.dumps({
            "action": "extract_sections",
            "text": text,
            "market": market,
            "data_len": 0,
        }).encode() + b"\n"

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", _WORKER_MODULE,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(header)

        if proc.returncode != 0:
            logger.warning("Section extract subprocess failed: %s", stderr.decode()[:500])
            return {}

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            logger.warning("Section extract subprocess invalid JSON: %s", stdout[:500])
            return {}

        if "error" in result:
            logger.warning("Section extract subprocess error: %s", result["error"])
            return {}

        return result.get("sections", {})
