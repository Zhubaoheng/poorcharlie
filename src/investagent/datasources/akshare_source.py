"""AkShare structured financial data source.

Provides deterministic (zero-hallucination) financial statement data
for A-shares and HK stocks via AkShare aggregated APIs (Sina/东财/同花顺).

This replaces LLM-based number extraction from PDF for standard
three-statement financials, EPS, and shares.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _parse_cn_number(val: Any) -> float | None:
    """Parse Chinese financial number strings like '893.35亿', '137.89亿', '68.64'."""
    if val is None or val is False or (isinstance(val, float) and val != val):  # NaN check
        return None
    s = str(val).strip()
    if not s or s == "False" or s == "nan" or s == "-":
        return None

    # Remove commas
    s = s.replace(",", "")

    # Chinese unit multipliers
    multiplier = 1.0
    if s.endswith("亿"):
        multiplier = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        multiplier = 1e4
        s = s[:-1]
    elif s.endswith("千") or s.endswith("仟"):
        multiplier = 1e3
        s = s[:-1]

    try:
        return float(s) * multiplier
    except (ValueError, TypeError):
        return None


def fetch_a_share_financials(symbol: str, years: int = 7) -> dict[str, Any]:
    """Fetch A-share financial statements from AkShare (同花顺 source).

    Args:
        symbol: Stock code without prefix (e.g., "600519")
        years: Number of annual reports to fetch

    Returns dict with keys: income_statement, balance_sheet, cash_flow,
    each containing a list of row dicts ready for FilingOutput schema.
    """
    import akshare as ak

    code = re.sub(r"[^\d]", "", symbol.split(".")[0])
    result: dict[str, Any] = {
        "income_statement": [],
        "balance_sheet": [],
        "cash_flow": [],
        "source": "akshare_ths",
    }

    # --- Income Statement ---
    try:
        df = ak.stock_financial_benefit_ths(symbol=code, indicator="按报告期")
        # Filter to annual reports only
        annual = df[df["报告期"].str.endswith("12-31")].head(years)

        for _, row in annual.iterrows():
            fiscal_year = str(row["报告期"])[:4]
            result["income_statement"].append({
                "fiscal_year": fiscal_year,
                "fiscal_period": "FY",
                "revenue": _parse_cn_number(row.get("其中：营业收入") or row.get("*营业总收入")),
                "cost_of_revenue": _parse_cn_number(row.get("其中：营业成本")),
                "gross_profit": None,  # will be computed in post-processing
                "rd_expense": _parse_cn_number(row.get("研发费用")),
                "sga_expense": _parse_cn_number(row.get("销售费用")),
                "depreciation_amortization": None,
                "operating_income": _parse_cn_number(row.get("三、营业利润")),
                "interest_expense": _parse_cn_number(row.get("其中：利息费用")),
                "tax_provision": _parse_cn_number(row.get("减：所得税费用")),
                "net_income": _parse_cn_number(row.get("*净利润") or row.get("五、净利润")),
                "net_income_to_parent": _parse_cn_number(row.get("*归属于母公司所有者的净利润") or row.get("归属于母公司所有者的净利润")),
                "eps_basic": _parse_cn_number(row.get("（一）基本每股收益")),
                "eps_diluted": _parse_cn_number(row.get("（二）稀释每股收益")),
                "shares_basic": None,  # derived from net_income / eps
                "shares_diluted": None,
            })
        logger.info("AkShare A-share IS: %d rows for %s", len(result["income_statement"]), code)
    except Exception:
        logger.warning("AkShare A-share IS failed for %s", code, exc_info=True)

    # --- Balance Sheet ---
    try:
        df = ak.stock_financial_debt_ths(symbol=code, indicator="按报告期")
        annual = df[df["报告期"].str.endswith("12-31")].head(years)

        for _, row in annual.iterrows():
            fiscal_year = str(row["报告期"])[:4]
            result["balance_sheet"].append({
                "fiscal_year": fiscal_year,
                "cash_and_equivalents": _parse_cn_number(row.get("货币资金") or row.get("总现金")),
                "short_term_investments": _parse_cn_number(row.get("交易性金融资产")),
                "accounts_receivable": _parse_cn_number(row.get("应收账款")),
                "inventory": _parse_cn_number(row.get("存货")),
                "total_current_assets": _parse_cn_number(row.get("流动资产合计")),
                "ppe_net": _parse_cn_number(row.get("固定资产合计") or row.get("固定资产")),
                "goodwill": _parse_cn_number(row.get("商誉")),
                "intangible_assets": _parse_cn_number(row.get("无形资产")),
                "total_assets": _parse_cn_number(row.get("*资产合计") or row.get("资产合计")),
                "accounts_payable": _parse_cn_number(row.get("应付账款")),
                "short_term_debt": _parse_cn_number(row.get("短期借款")),
                "total_current_liabilities": _parse_cn_number(row.get("流动负债合计")),
                "long_term_debt": _parse_cn_number(row.get("长期借款")),
                "total_liabilities": _parse_cn_number(row.get("*负债合计") or row.get("负债合计")),
                "shareholders_equity": _parse_cn_number(row.get("*归属于母公司所有者权益合计") or row.get("归属于母公司的股东权益")),
                "minority_interest": _parse_cn_number(row.get("少数股东权益")),
            })
        logger.info("AkShare A-share BS: %d rows for %s", len(result["balance_sheet"]), code)
    except Exception:
        logger.warning("AkShare A-share BS failed for %s", code, exc_info=True)

    # --- Cash Flow ---
    try:
        df = ak.stock_financial_cash_ths(symbol=code, indicator="按报告期")
        annual = df[df["报告期"].str.endswith("12-31")].head(years)

        for _, row in annual.iterrows():
            fiscal_year = str(row["报告期"])[:4]
            ocf = _parse_cn_number(row.get("经营活动产生的现金流量净额"))
            capex = _parse_cn_number(row.get("购建固定资产、无形资产和其他长期资产支付的现金"))
            div = _parse_cn_number(row.get("分配股利、利润或偿付利息支付的现金"))

            result["cash_flow"].append({
                "fiscal_year": fiscal_year,
                "operating_cash_flow": ocf,
                "capex": abs(capex) if capex else None,
                "free_cash_flow": (ocf - abs(capex)) if ocf is not None and capex is not None else None,
                "dividends_paid": div,
                "buyback_amount": _parse_cn_number(row.get("回购股票")),
                "debt_issued": _parse_cn_number(row.get("取得借款收到的现金")),
                "debt_repaid": _parse_cn_number(row.get("偿还债务支付的现金")),
                "acquisitions": None,
            })
        logger.info("AkShare A-share CF: %d rows for %s", len(result["cash_flow"]), code)
    except Exception:
        logger.warning("AkShare A-share CF failed for %s", code, exc_info=True)

    return result


def fetch_hk_financials(symbol: str, years: int = 7) -> dict[str, Any]:
    """Fetch HK stock financial indicators from AkShare (东财 source).

    Note: Full three-statement data may not be available for HK stocks via AkShare.
    Falls back to key indicators (EPS, ROE, margins) from analysis API.
    """
    import akshare as ak

    code = re.sub(r"[^\d]", "", symbol.split(".")[0]).zfill(5)
    result: dict[str, Any] = {
        "income_statement": [],
        "balance_sheet": [],
        "cash_flow": [],
        "indicators": [],
        "source": "akshare_hk_em",
    }

    try:
        df = ak.stock_financial_hk_analysis_indicator_em(symbol=code)
        df = df.head(years)

        for _, row in df.iterrows():
            report_date = str(row.get("REPORT_DATE", ""))[:10]
            fiscal_year = report_date[:4]

            result["indicators"].append({
                "fiscal_year": fiscal_year,
                "eps_basic": row.get("BASIC_EPS"),
                "eps_diluted": row.get("DILUTED_EPS"),
                "eps_ttm": row.get("EPS_TTM"),
                "operate_income": row.get("OPERATE_INCOME"),
                "gross_profit": row.get("GROSS_PROFIT"),
                "holder_profit": row.get("HOLDER_PROFIT"),
                "gross_profit_ratio": row.get("GROSS_PROFIT_RATIO"),
                "net_profit_ratio": row.get("NET_PROFIT_RATIO"),
                "roe_avg": row.get("ROE_AVG"),
                "roa": row.get("ROA"),
                "bps": row.get("BPS"),
                "debt_asset_ratio": row.get("DEBT_ASSET_RATIO"),
                "current_ratio": row.get("CURRENT_RATIO"),
                "ocf_sales": row.get("OCF_SALES"),
                "roic_yearly": row.get("ROIC_YEARLY"),
            })

        logger.info("AkShare HK indicators: %d rows for %s", len(result["indicators"]), code)
    except Exception:
        logger.warning("AkShare HK indicators failed for %s", code, exc_info=True)

    return result


async def fetch_structured_financials(
    ticker: str, market: str, years: int = 7,
) -> dict[str, Any]:
    """Async wrapper: fetch structured financials from AkShare.

    Returns empty dict if market not supported or API fails.
    """
    if market == "A_SHARE":
        return await asyncio.to_thread(fetch_a_share_financials, ticker, years)
    elif market == "HK":
        return await asyncio.to_thread(fetch_hk_financials, ticker, years)
    # US_ADR uses edgartools XBRL (handled separately)
    return {}
