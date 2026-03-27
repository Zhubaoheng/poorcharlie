"""CLI entry point for InvestAgent.

Usage:
    investagent 1448.HK
    investagent 600519.SH
    investagent BABA
    investagent 000858.SZ --name 五粮液 --sector 白酒
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

from investagent.config import Settings
from investagent.llm import LLMClient
from investagent.report import generate_debug_log, generate_report
from investagent.schemas.company import CompanyIntake
from investagent.workflow.orchestrator import run_pipeline

# Ticker suffix → exchange mapping
_SUFFIX_MAP: dict[str, str] = {
    ".HK": "HKEX",
    ".SS": "SSE",
    ".SH": "SSE",
    ".SZ": "SZSE",
    ".BJ": "BSE",
}


def _parse_ticker(raw: str) -> tuple[str, str]:
    """Parse 'BABA', '1448.HK', '600519.SH' into (ticker, exchange).

    Rules:
    - Has suffix (.HK, .SH, .SZ, .SS, .BJ) → split and map
    - Pure digits, starts with 6/9 → SSE
    - Pure digits, starts with 0/3/2 → SZSE
    - Pure digits, starts with 4/8 → BSE
    - Pure digits, 4-5 digits → HKEX
    - Otherwise (letters) → NYSE
    """
    upper = raw.upper()

    # Check for known suffix
    for suffix, exchange in _SUFFIX_MAP.items():
        if upper.endswith(suffix):
            ticker = raw[: -len(suffix)]
            return ticker, exchange

    # No suffix — infer from pattern
    digits_only = re.fullmatch(r"\d+", raw)
    if digits_only:
        code = raw
        if len(code) == 6:
            if code[0] in ("6", "9"):
                return code, "SSE"
            elif code[0] in ("0", "3", "2"):
                return code, "SZSE"
            elif code[0] in ("4", "8"):
                return code, "BSE"
        # 4-5 digit codes are typically HK
        if 4 <= len(code) <= 5:
            return code, "HKEX"

    # Default: US market
    return raw, "NYSE"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="investagent",
        description="Munger-style value investing multi-agent system",
        usage="investagent <ticker> [options]\n\n"
        "Examples:\n"
        "  investagent 1448.HK\n"
        "  investagent 600519.SH --name 贵州茅台\n"
        "  investagent BABA\n"
        "  investagent 000858.SZ --name 五粮液 --sector 白酒",
    )
    parser.add_argument("ticker", help="Stock ticker (e.g., 1448.HK, 600519.SH, BABA)")
    parser.add_argument("--name", default=None, help="Company name (auto-detected if omitted)")
    parser.add_argument("--sector", default=None, help="Industry sector")
    parser.add_argument("--notes", default=None, help="Additional notes")
    parser.add_argument("--output-dir", default="output", help="Output directory (default: output/)")
    return parser


async def _run(args: argparse.Namespace) -> None:
    ticker, exchange = _parse_ticker(args.ticker)
    name = args.name or ticker

    intake = CompanyIntake(
        ticker=ticker,
        name=name,
        exchange=exchange,
        sector=args.sector,
        notes=args.notes,
    )

    settings = Settings()
    llm = LLMClient(
        model=settings.model_name,
        base_url=settings.api_base_url,
        api_key=settings.api_key,
    )

    print(f"开始分析: {name} ({ticker}.{exchange})")
    print(f"LLM: {settings.provider} / {settings.model_name}")
    print("=" * 60)

    t0 = time.time()

    try:
        ctx = await run_pipeline(intake, llm=llm)
    except Exception as e:
        print(f"\n❌ Pipeline 失败: {type(e).__name__}: {e}")
        sys.exit(1)

    elapsed = time.time() - t0

    report = generate_report(ctx, elapsed=elapsed)
    debug_log = generate_debug_log(ctx, elapsed=elapsed)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    # Save Markdown report
    report_path = output_dir / f"{ticker}_{timestamp}.md"
    report_path.write_text(report, encoding="utf-8")

    # Save JSON debug log (full agent input/output, no truncation)
    log_path = output_dir / f"{ticker}_{timestamp}_debug.json"
    log_path.write_text(debug_log, encoding="utf-8")

    print(f"\n完成: {len(ctx.completed_agents())} 个 Agent | {elapsed:.0f}s")

    if ctx.is_stopped():
        print(f"⛔ 停止原因: {ctx.stop_reason}")
    else:
        committee = ctx.get_result("committee") if "committee" in ctx.completed_agents() else None
        if committee:
            print(f"结论: {committee.final_label.value}")

    print(f"报告: {report_path}")
    print(f"日志: {log_path}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
