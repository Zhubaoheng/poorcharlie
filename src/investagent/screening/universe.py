"""Stock universe construction and exclusion rules.

Builds the eligible stock pool for a given market by fetching the full
listing from AkShare, applying rule-based exclusions (ST, financials,
disclosure), and optionally LLM-based exclusions (opaque tech, shells,
young companies).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Keywords for financial sector exclusion (substring match).
_FINANCIAL_KEYWORDS = ("银行", "保险", "证券", "金融", "信托", "期货", "租赁")

# Minimum number of annual reports required.
_MIN_ANNUAL_REPORTS = 3


# ---------------------------------------------------------------------------
# AkShare data fetching
# ---------------------------------------------------------------------------

def _fetch_a_share_list() -> list[dict[str, Any]]:
    """Fetch all A-share stocks with basic info from AkShare."""
    import akshare as ak

    df = ak.stock_info_a_code_name()
    stocks: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        stocks.append({
            "ticker": str(row.get("code", "")),
            "name": str(row.get("name", "")),
        })
    return stocks


def _build_industry_map_bulk() -> dict[str, str]:
    """Build ticker->industry map in bulk. Fallback chain:

    1. stock_board_industry_cons_em (东财板块, push2.eastmoney.com)
    2. sw_index_third_cons (申万一级, legulegu.com)
    """
    import akshare as ak

    # Try eastmoney boards first
    try:
        industry_map: dict[str, str] = {}
        boards = ak.stock_board_industry_name_em()
        for _, row in boards.iterrows():
            board_name = str(row.get("板块名称", ""))
            try:
                cons = ak.stock_board_industry_cons_em(symbol=board_name)
                for _, cr in cons.iterrows():
                    ticker = str(cr["代码"])
                    if ticker not in industry_map:
                        industry_map[ticker] = board_name
            except Exception:
                pass
        if len(industry_map) > 1000:
            logger.info("Industry map (EM boards): %d tickers", len(industry_map))
            return industry_map
    except Exception:
        pass
    logger.warning("EM industry map failed or incomplete, trying Shenwan...")

    # Fallback: Shenwan L1
    try:
        industry_map = {}
        ind = ak.sw_index_first_info()
        for _, row in ind.iterrows():
            code = str(row["行业代码"])
            name = str(row["行业名称"])
            try:
                cons = ak.sw_index_third_cons(symbol=code)
                for _, cr in cons.iterrows():
                    ticker = str(cr["股票代码"]).zfill(6)
                    if ticker not in industry_map:
                        industry_map[ticker] = name
            except Exception:
                pass
        logger.info("Industry map (Shenwan): %d tickers", len(industry_map))
        return industry_map
    except Exception:
        logger.warning("All industry map sources failed")
        return {}


def _enrich_industry(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add industry classification to each stock (bulk, not per-stock)."""
    industry_map = _build_industry_map_bulk()
    for stock in stocks:
        if not stock.get("industry"):
            stock["industry"] = industry_map.get(stock["ticker"], "")
    return stocks


# ---------------------------------------------------------------------------
# Rule-based exclusions
# ---------------------------------------------------------------------------

def apply_rule_exclusions(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply deterministic rule-based exclusions. Pure Python, no LLM.

    Excludes:
    - ST / *ST stocks (name prefix)
    - Financial sector (Shenwan level-1: 银行, 非银金融)
    - Stocks with fewer than 3 years of annual report disclosure
    """
    result = []
    for stock in stocks:
        name = stock.get("name", "")

        # ST / *ST
        if re.match(r"^[\*]?ST", name):
            stock["excluded"] = True
            stock["exclude_reason"] = "ST/*ST"
            continue

        # Financial sector (substring match)
        industry = stock.get("industry", "")
        if any(kw in industry for kw in _FINANCIAL_KEYWORDS):
            stock["excluded"] = True
            stock["exclude_reason"] = f"金融类: {industry}"
            continue

        # Disclosure < 3 years (if annual_report_count is populated)
        report_count = stock.get("annual_report_count")
        if report_count is not None and report_count < _MIN_ANNUAL_REPORTS:
            stock["excluded"] = True
            stock["exclude_reason"] = f"年报不足{_MIN_ANNUAL_REPORTS}年"
            continue

        result.append(stock)

    excluded_count = len(stocks) - len(result)
    logger.info(
        "Rule exclusions: %d excluded, %d remaining (from %d)",
        excluded_count, len(result), len(stocks),
    )
    return result


# ---------------------------------------------------------------------------
# LLM-based exclusions
# ---------------------------------------------------------------------------

_EXCLUSION_SYSTEM_PROMPT = """你是一个股票筛选助手。你的任务是判断一家公司是否应该被排除出投资分析范围。

排除标准：
1. 创立时间不足5年的公司
2. 不透明科技公司（军工、尖端材料、创新药等普通投资者难以理解的行业）
3. 壳公司或资产极度空洞的公司（无实质业务、营收极低、员工极少）

注意：排除标准是保守的。如果你不确定，应该保留（KEEP）而不是排除。
只排除明确符合上述标准的公司。"""

_EXCLUSION_TOOL = {
    "name": "exclusion_decision",
    "description": "Output exclusion decision for a company",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["EXCLUDE", "KEEP"],
                "description": "是否排除该公司",
            },
            "reason": {
                "type": "string",
                "description": "排除或保留的简短理由（20字以内）",
            },
        },
        "required": ["decision", "reason"],
    },
}


def _format_stock_for_llm(stock: dict[str, Any]) -> str:
    """Format a stock's basic info as a short text block for LLM input."""
    parts = [
        f"股票代码: {stock.get('ticker', 'N/A')}",
        f"名称: {stock.get('name', 'N/A')}",
    ]
    if stock.get("industry"):
        parts.append(f"行业: {stock['industry']}")
    if stock.get("listing_date"):
        parts.append(f"上市日期: {stock['listing_date']}")
    if stock.get("main_business"):
        parts.append(f"主营业务: {stock['main_business']}")
    if stock.get("total_assets"):
        parts.append(f"总资产: {stock['total_assets']}")
    if stock.get("revenue"):
        parts.append(f"营收: {stock['revenue']}")
    return "\n".join(parts)


async def apply_llm_exclusions(
    stocks: list[dict[str, Any]],
    llm: Any,
    *,
    batch_size: int = 10,
) -> list[dict[str, Any]]:
    """Apply LLM-based contextual exclusions.

    Uses the provided LLM client to judge whether each stock should be
    excluded (opaque tech, shell company, too young). Processes in batches.

    Args:
        stocks: list of stock dicts to evaluate
        llm: LLMClient instance (MiniMax or other)
        batch_size: number of concurrent LLM calls
    """
    result = []
    excluded = 0

    async def _check_one(stock: dict[str, Any]) -> dict[str, Any]:
        prompt = _format_stock_for_llm(stock)
        try:
            response = await llm.create_message(
                system=_EXCLUSION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                tools=[_EXCLUSION_TOOL],
                max_tokens=256,
            )
            for block in response.content:
                if block.type == "tool_use":
                    decision = block.input.get("decision", "KEEP")
                    reason = block.input.get("reason", "")
                    if decision == "EXCLUDE":
                        stock["excluded"] = True
                        stock["exclude_reason"] = f"LLM: {reason}"
                    return stock
        except Exception:
            logger.warning("LLM exclusion failed for %s, keeping", stock.get("ticker"))
        return stock

    # Process in batches
    for i in range(0, len(stocks), batch_size):
        batch = stocks[i : i + batch_size]
        results = await asyncio.gather(*[_check_one(s) for s in batch])
        for stock in results:
            if stock.get("excluded"):
                excluded += 1
            else:
                result.append(stock)

    logger.info(
        "LLM exclusions: %d excluded, %d remaining (from %d)",
        excluded, len(result), len(stocks),
    )
    return result


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def build_universe(
    market: str = "A_SHARE",
    llm: Any | None = None,
) -> list[dict[str, Any]]:
    """Build the eligible stock universe for screening.

    1. Fetch all stocks for the market
    2. Apply rule-based exclusions
    3. If LLM provided, apply contextual exclusions

    Returns list of stock dicts that passed all exclusion filters.
    """
    if market != "A_SHARE":
        raise NotImplementedError(f"Market {market!r} not yet supported")

    stocks = await asyncio.to_thread(_fetch_a_share_list)
    logger.info("Fetched %d A-share stocks", len(stocks))

    stocks = apply_rule_exclusions(stocks)

    if llm is not None:
        stocks = await apply_llm_exclusions(stocks, llm)

    logger.info("Final universe: %d stocks", len(stocks))
    return stocks
