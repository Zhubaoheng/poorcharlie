"""Financial ratio calculation from structured three-statement data.

Pure Python — no LLM, no external API calls. Takes IncomeStatementRow,
BalanceSheetRow, CashFlowRow lists (from AkShare or filing extraction)
and computes key financial ratios for screening.
"""

from __future__ import annotations

from investagent.schemas.filing import (
    BalanceSheetRow,
    CashFlowRow,
    IncomeStatementRow,
)

# Default corporate tax rate (China) used when actual rate cannot be derived.
_DEFAULT_TAX_RATE = 0.25


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def _yoy_growth(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def _effective_tax_rate(row: IncomeStatementRow) -> float:
    """Estimate effective tax rate from income statement, fallback to 25%."""
    if row.tax_provision is None or row.operating_income is None:
        return _DEFAULT_TAX_RATE
    pre_tax = row.operating_income
    if row.interest_expense is not None:
        pre_tax = pre_tax - abs(row.interest_expense)
    if pre_tax <= 0:
        return _DEFAULT_TAX_RATE
    rate = abs(row.tax_provision) / pre_tax
    if rate < 0 or rate > 0.6:
        return _DEFAULT_TAX_RATE
    return rate


def _align_by_year(
    income: list[IncomeStatementRow],
    balance: list[BalanceSheetRow],
    cash_flow: list[CashFlowRow],
) -> list[
    tuple[str, IncomeStatementRow | None, BalanceSheetRow | None, CashFlowRow | None]
]:
    """Align three statements by fiscal_year, sorted ascending."""
    years: set[str] = set()
    is_map = {}
    bs_map = {}
    cf_map = {}
    for r in income:
        years.add(r.fiscal_year)
        is_map[r.fiscal_year] = r
    for r in balance:
        years.add(r.fiscal_year)
        bs_map[r.fiscal_year] = r
    for r in cash_flow:
        years.add(r.fiscal_year)
        cf_map[r.fiscal_year] = r
    return [
        (y, is_map.get(y), bs_map.get(y), cf_map.get(y))
        for y in sorted(years)
    ]


def compute_ratios(
    income: list[IncomeStatementRow],
    balance: list[BalanceSheetRow],
    cash_flow: list[CashFlowRow],
    market_cap: float | None = None,
) -> dict[str, list[float | None]]:
    """Compute financial ratios from three-statement data.

    Returns a dict keyed by ratio name. Every value list has the same
    length and aligns with the ``fiscal_years`` key.
    """
    aligned = _align_by_year(income, balance, cash_flow)
    n = len(aligned)

    out: dict[str, list[float | None]] = {
        "fiscal_years": [],  # type: ignore[list-item]
        # Profitability
        "roe": [],
        "roic": [],
        "gross_margin": [],
        "net_margin": [],
        # Growth (YoY)
        "revenue_growth": [],
        "net_income_growth": [],
        "eps_growth": [],
        # Cash quality
        "ocf_to_ni": [],
        "fcf_to_ni": [],
        "capex_to_revenue": [],
        # Leverage
        "debt_to_assets": [],
        "net_debt_to_ebit": [],
        "interest_coverage": [],
        # Valuation (only if market_cap provided)
        "pe_ttm": [],
        "pb": [],
        "dividend_yield": [],
    }

    for i, (year, is_row, bs_row, cf_row) in enumerate(aligned):
        out["fiscal_years"].append(year)  # type: ignore[arg-type]

        # --- Profitability ---
        ni = None
        revenue = None
        gross_profit = None
        operating_income = None

        if is_row is not None:
            ni = is_row.net_income_to_parent if is_row.net_income_to_parent is not None else is_row.net_income
            revenue = is_row.revenue
            operating_income = is_row.operating_income
            gross_profit = is_row.gross_profit
            if gross_profit is None and revenue is not None and is_row.cost_of_revenue is not None:
                gross_profit = revenue - abs(is_row.cost_of_revenue)

        equity = bs_row.shareholders_equity if bs_row else None
        out["roe"].append(_safe_div(ni, equity))
        out["gross_margin"].append(_safe_div(gross_profit, revenue))
        out["net_margin"].append(_safe_div(ni, revenue))

        # ROIC = NOPAT / invested_capital
        roic_val = None
        if is_row is not None and bs_row is not None and operating_income is not None:
            tax_rate = _effective_tax_rate(is_row)
            nopat = operating_income * (1 - tax_rate)
            total_debt = (bs_row.short_term_debt or 0) + (bs_row.long_term_debt or 0)
            cash = bs_row.cash_and_equivalents or 0
            invested_capital = (equity or 0) + total_debt - cash
            roic_val = _safe_div(nopat, invested_capital)
        out["roic"].append(roic_val)

        # --- Growth (YoY) ---
        if i == 0:
            out["revenue_growth"].append(None)
            out["net_income_growth"].append(None)
            out["eps_growth"].append(None)
        else:
            prev_is = aligned[i - 1][1]
            prev_ni = None
            prev_revenue = None
            prev_eps = None
            if prev_is is not None:
                prev_ni = prev_is.net_income_to_parent if prev_is.net_income_to_parent is not None else prev_is.net_income
                prev_revenue = prev_is.revenue
                prev_eps = prev_is.eps_basic
            cur_eps = is_row.eps_basic if is_row else None
            out["revenue_growth"].append(_yoy_growth(revenue, prev_revenue))
            out["net_income_growth"].append(_yoy_growth(ni, prev_ni))
            out["eps_growth"].append(_yoy_growth(cur_eps, prev_eps))

        # --- Cash quality ---
        ocf = cf_row.operating_cash_flow if cf_row else None
        fcf = cf_row.free_cash_flow if cf_row else None
        capex = cf_row.capex if cf_row else None
        out["ocf_to_ni"].append(_safe_div(ocf, ni))
        out["fcf_to_ni"].append(_safe_div(fcf, ni))
        out["capex_to_revenue"].append(_safe_div(capex, revenue))

        # --- Leverage ---
        total_assets = bs_row.total_assets if bs_row else None
        total_liabilities = bs_row.total_liabilities if bs_row else None
        out["debt_to_assets"].append(_safe_div(total_liabilities, total_assets))

        if bs_row is not None and operating_income is not None:
            total_debt = (bs_row.short_term_debt or 0) + (bs_row.long_term_debt or 0)
            cash = bs_row.cash_and_equivalents or 0
            net_debt = total_debt - cash
            out["net_debt_to_ebit"].append(_safe_div(net_debt, operating_income))
        else:
            out["net_debt_to_ebit"].append(None)

        interest = abs(is_row.interest_expense) if is_row and is_row.interest_expense else None
        out["interest_coverage"].append(_safe_div(operating_income, interest))

        # --- Valuation ---
        if market_cap is not None:
            out["pe_ttm"].append(_safe_div(market_cap, ni))
            out["pb"].append(_safe_div(market_cap, equity))
            div_paid = abs(cf_row.dividends_paid) if cf_row and cf_row.dividends_paid else None
            out["dividend_yield"].append(_safe_div(div_paid, market_cap))
        else:
            out["pe_ttm"].append(None)
            out["pb"].append(None)
            out["dividend_yield"].append(None)

    return out


# ---------------------------------------------------------------------------
# Quantitative pre-filter — hard floors before LLM screening
# ---------------------------------------------------------------------------

def should_skip_by_ratios(ratios: dict[str, list[float | None]]) -> str | None:
    """Return a skip reason if ratios fail hard quantitative floors.

    Returns None if the stock passes (should go to LLM screening).
    These are absolute minimums that no industry context changes:
    a Munger-quality company never looks like this.
    """
    def _avg(vals: list[float | None]) -> float | None:
        clean = [v for v in vals if v is not None]
        return sum(clean) / len(clean) if clean else None

    def _consecutive_negative(vals: list[float | None], n: int) -> bool:
        count = 0
        for v in vals:
            if v is not None and v < 0:
                count += 1
                if count >= n:
                    return True
            else:
                count = 0
        return False

    roe = ratios.get("roe", [])
    roic = ratios.get("roic", [])
    net_margin = ratios.get("net_margin", [])
    ocf_to_ni = ratios.get("ocf_to_ni", [])
    revenue_growth = ratios.get("revenue_growth", [])

    # Consecutive losses: net margin < 0 for 3+ years
    if _consecutive_negative(net_margin, 3):
        return "连续3年以上净亏损"

    # Chronically low returns: avg ROE < 3% AND avg ROIC < 5%
    avg_roe = _avg(roe)
    avg_roic = _avg(roic)
    if avg_roe is not None and avg_roic is not None:
        if avg_roe < 0.03 and avg_roic < 0.05:
            return f"长期低回报: avg ROE {avg_roe:.1%}, avg ROIC {avg_roic:.1%}"

    # Revenue shrinking for 3+ consecutive years
    if _consecutive_negative(revenue_growth, 3):
        return "营收连续3年以上下滑"

    # Chronically poor cash conversion: avg OCF/NI < 0.3
    avg_ocf = _avg(ocf_to_ni)
    if avg_ocf is not None and avg_ocf < 0.3:
        # But exempt if revenue is growing fast (investment phase)
        avg_rev_g = _avg(revenue_growth)
        if avg_rev_g is None or avg_rev_g < 0.15:
            return f"长期现金转换差: avg OCF/NI {avg_ocf:.1%}"

    return None
