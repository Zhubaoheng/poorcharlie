#!/usr/bin/env python3
"""Quick LLM profile sanity check.

Usage:
    uv run python scripts/llm_diag.py              # test all configured profiles
    uv run python scripts/llm_diag.py claude       # test only specified profiles

Reports each profile's status (ok / error), model name, and single-call latency.
Safe to run during production pipelines; uses a minimal 16-token probe.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from poorcharlie.config import create_llm_client  # noqa: E402
from poorcharlie.llm_profiles import (  # noqa: E402
    list_available_profiles,
    resolve_default_profile,
)


async def ping(profile: str) -> dict:
    try:
        llm = create_llm_client(profile=profile)
    except Exception as e:
        return {"profile": profile, "ok": False, "error": f"config: {type(e).__name__}: {e}"}

    t0 = time.time()
    try:
        response = await llm.create_message(
            system="Respond with exactly one word: ok",
            messages=[{"role": "user", "content": "Are you alive?"}],
            tools=[],
            max_tokens=16,
        )
        elapsed = round(time.time() - t0, 2)
        content_preview = ""
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "text":
                content_preview = (block.text or "")[:40]
                break
        return {
            "profile": profile,
            "ok": True,
            "latency_s": elapsed,
            "model": llm.model,
            "provider": llm.provider,
            "reply": content_preview,
        }
    except Exception as e:
        return {
            "profile": profile,
            "ok": False,
            "latency_s": round(time.time() - t0, 2),
            "error": f"{type(e).__name__}: {e}",
        }


async def main() -> None:
    args = [p for p in sys.argv[1:] if not p.startswith("-")]
    profiles = args if args else list_available_profiles()
    default = resolve_default_profile()

    print(f"Default profile: {default}")
    print(f"Configured profiles: {list_available_profiles()}")
    if not profiles:
        print("(no profiles to test — configure env vars per .env.example)")
        return
    print(f"Testing: {profiles}\n")

    for p in profiles:
        result = await ping(p)
        status = "✓" if result["ok"] else "✗"
        if result["ok"]:
            print(
                f"{status} {p:10s}  {result['latency_s']:>5.2f}s  "
                f"{result['model']} ({result['provider']})  reply={result['reply']!r}"
            )
        else:
            print(f"{status} {p:10s}  ERROR: {result['error']}")


if __name__ == "__main__":
    asyncio.run(main())
