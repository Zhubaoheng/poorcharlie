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


def _pivot_hk_long_format(df: Any, years: int) -> dict[str, dict[str, float | None]]:
    """Pivot AkShare HK long-format data to {report_date: {item_name: amount}}.

    AkShare returns HK data as long format: one row per (report_date, item).
    We pivot to one dict per report_date for easy mapping.
    """
    result: dict[str, dict[str, float | None]] = {}
    for _, row in df.iterrows():
        report_date = str(row.get("REPORT_DATE", ""))[:10]
        fiscal_year = report_date[:4]
        item_name = str(row.get("STD_ITEM_NAME", ""))
        amount = row.get("AMOUNT")

        if fiscal_year not in result:
            result[fiscal_year] = {}
        if amount is not None and amount == amount:  # NaN check
            result[fiscal_year][item_name] = float(amount)

    # Keep only latest N years
    sorted_years = sorted(result.keys(), reverse=True)[:years]
    return {y: result[y] for y in sorted_years}


# HK item name → our schema field mapping
_HK_IS_MAP: dict[str, str] = {
    "营业额": "revenue",
    "营运收入": "revenue",
    "销售成本": "cost_of_revenue",
    "毛利": "gross_profit",
    "研发费用": "rd_expense",
    "销售及分销费用": "sga_expense",
    "销售费用": "sga_expense",
    "折旧及摊销": "depreciation_amortization",
    "经营溢利": "operating_income",
    "营运利润": "operating_income",
    "融资成本": "interest_expense",
    "税项": "tax_provision",
    "所得税": "tax_provision",
    "除税后溢利": "net_income",
    "净利润": "net_income",
    "持续经营业务税后利润": "net_income",
    "股东应占溢利": "net_income_to_parent",
    "公司拥有人应占利润": "net_income_to_parent",
    "每股基本盈利": "eps_basic",
    "基本每股收益": "eps_basic",
    "每股摊薄盈利": "eps_diluted",
    "稀释每股收益": "eps_diluted",
}

_HK_BS_MAP: dict[str, str] = {
    "物业厂房及设备": "ppe_net",
    "物业、厂房及设备": "ppe_net",
    "商誉": "goodwill",
    "无形资产": "intangible_assets",
    "存货": "inventory",
    "应收帐款": "accounts_receivable",
    "应收账款": "accounts_receivable",
    "现金及等价物": "cash_and_equivalents",
    "银行结余及现金": "cash_and_equivalents",
    "流动资产合计": "total_current_assets",
    "非流动资产合计": "total_current_assets",  # fallback
    "总资产": "total_assets",
    "资产总值": "total_assets",
    "资产总额": "total_assets",
    "应付帐款": "accounts_payable",
    "应付账款": "accounts_payable",
    "短期借款": "short_term_debt",
    "短期贷款": "short_term_debt",
    "流动负债合计": "total_current_liabilities",
    "长期贷款": "long_term_debt",
    "非流动负债合计": "long_term_debt",
    "总负债": "total_liabilities",
    "负债总值": "total_liabilities",
    "负债总额": "total_liabilities",
    "股东权益": "shareholders_equity",
    "权益总额": "shareholders_equity",
    "净资产": "shareholders_equity",
    "少数股东权益": "minority_interest",
}

_HK_CF_MAP: dict[str, str] = {
    "经营业务现金净额": "operating_cash_flow",
    "经营活动产生的现金流量净额": "operating_cash_flow",
    "购买物业、厂房及设备": "capex",
    "购建固定资产": "capex",
    "已付股利": "dividends_paid",
    "已付股息": "dividends_paid",
    "已付股息(融资)": "dividends_paid",
    "回购股份": "buyback_amount",
    "偿还借贷": "debt_repaid",
    "偿还借款": "debt_repaid",
    "新增借贷": "debt_issued",
    "新增借款": "debt_issued",
    "收购附属公司": "acquisitions",
}


def fetch_hk_financials(symbol: str, years: int = 7) -> dict[str, Any]:
    """Fetch HK stock full three statements from AkShare (东财 source).

    Uses stock_financial_hk_report_em with long-format pivot.
    """
    import akshare as ak

    code = re.sub(r"[^\d]", "", symbol.split(".")[0]).zfill(5)
    result: dict[str, Any] = {
        "income_statement": [],
        "balance_sheet": [],
        "cash_flow": [],
        "source": "akshare_hk_em",
    }

    # --- Income Statement ---
    try:
        df = ak.stock_financial_hk_report_em(stock=code, symbol="利润表", indicator="年度")
        pivoted = _pivot_hk_long_format(df, years)

        for fiscal_year, items in sorted(pivoted.items()):
            row: dict[str, Any] = {"fiscal_year": fiscal_year, "fiscal_period": "FY"}
            for cn_name, field in _HK_IS_MAP.items():
                if cn_name in items and field not in row:
                    val = items[cn_name]
                    # cost fields are positive in source, negate for consistency
                    if field in ("cost_of_revenue", "interest_expense", "tax_provision", "sga_expense", "rd_expense"):
                        val = -abs(val) if val > 0 else val
                    row[field] = val
            row.setdefault("shares_basic", None)
            row.setdefault("shares_diluted", None)
            result["income_statement"].append(row)

        logger.info("AkShare HK IS: %d rows for %s", len(result["income_statement"]), code)
    except Exception:
        logger.warning("AkShare HK IS failed for %s", code, exc_info=True)

    # --- Balance Sheet ---
    try:
        df = ak.stock_financial_hk_report_em(stock=code, symbol="资产负债表", indicator="年度")
        pivoted = _pivot_hk_long_format(df, years)

        for fiscal_year, items in sorted(pivoted.items()):
            row = {"fiscal_year": fiscal_year}
            for cn_name, field in _HK_BS_MAP.items():
                if cn_name in items and field not in row:
                    row[field] = items[cn_name]
            result["balance_sheet"].append(row)

        logger.info("AkShare HK BS: %d rows for %s", len(result["balance_sheet"]), code)
    except Exception:
        logger.warning("AkShare HK BS failed for %s", code, exc_info=True)

    # --- Cash Flow ---
    try:
        df = ak.stock_financial_hk_report_em(stock=code, symbol="现金流量表", indicator="年度")
        pivoted = _pivot_hk_long_format(df, years)

        for fiscal_year, items in sorted(pivoted.items()):
            row: dict[str, Any] = {"fiscal_year": fiscal_year}
            for cn_name, field in _HK_CF_MAP.items():
                if cn_name in items and field not in row:
                    val = items[cn_name]
                    if field == "capex":
                        val = abs(val)
                    row[field] = val
            # Derive FCF
            ocf = row.get("operating_cash_flow")
            capex = row.get("capex")
            if ocf is not None and capex is not None:
                row["free_cash_flow"] = ocf - abs(capex)
            result["cash_flow"].append(row)

        logger.info("AkShare HK CF: %d rows for %s", len(result["cash_flow"]), code)
    except Exception:
        logger.warning("AkShare HK CF failed for %s", code, exc_info=True)

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
