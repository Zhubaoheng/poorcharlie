"""Tests for investagent.screening.ratio_calc."""

from __future__ import annotations

import pytest

from investagent.schemas.filing import (
    BalanceSheetRow,
    CashFlowRow,
    IncomeStatementRow,
)
from investagent.screening.ratio_calc import compute_ratios


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is(
    year: str,
    revenue: float | None = None,
    cost_of_revenue: float | None = None,
    gross_profit: float | None = None,
    operating_income: float | None = None,
    interest_expense: float | None = None,
    tax_provision: float | None = None,
    net_income: float | None = None,
    net_income_to_parent: float | None = None,
    eps_basic: float | None = None,
) -> IncomeStatementRow:
    return IncomeStatementRow(
        fiscal_year=year,
        fiscal_period="FY",
        revenue=revenue,
        cost_of_revenue=cost_of_revenue,
        gross_profit=gross_profit,
        operating_income=operating_income,
        interest_expense=interest_expense,
        tax_provision=tax_provision,
        net_income=net_income,
        net_income_to_parent=net_income_to_parent,
        eps_basic=eps_basic,
    )


def _bs(
    year: str,
    total_assets: float | None = None,
    total_liabilities: float | None = None,
    shareholders_equity: float | None = None,
    short_term_debt: float | None = None,
    long_term_debt: float | None = None,
    cash_and_equivalents: float | None = None,
) -> BalanceSheetRow:
    return BalanceSheetRow(
        fiscal_year=year,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        shareholders_equity=shareholders_equity,
        short_term_debt=short_term_debt,
        long_term_debt=long_term_debt,
        cash_and_equivalents=cash_and_equivalents,
    )


def _cf(
    year: str,
    operating_cash_flow: float | None = None,
    capex: float | None = None,
    free_cash_flow: float | None = None,
    dividends_paid: float | None = None,
) -> CashFlowRow:
    return CashFlowRow(
        fiscal_year=year,
        operating_cash_flow=operating_cash_flow,
        capex=capex,
        free_cash_flow=free_cash_flow,
        dividends_paid=dividends_paid,
    )


# ---------------------------------------------------------------------------
# Test data: Maotai-like high-quality consumer company
# ---------------------------------------------------------------------------

def _maotai_data():
    income = [
        _is("2019", revenue=88e9, cost_of_revenue=8e9, operating_income=59e9,
            interest_expense=0, tax_provision=14e9, net_income=41e9,
            net_income_to_parent=41e9, eps_basic=32.80),
        _is("2020", revenue=95e9, cost_of_revenue=8.5e9, operating_income=64e9,
            interest_expense=0, tax_provision=15e9, net_income=47e9,
            net_income_to_parent=47e9, eps_basic=37.17),
        _is("2021", revenue=109e9, cost_of_revenue=9.5e9, operating_income=74e9,
            interest_expense=0, tax_provision=17e9, net_income=52e9,
            net_income_to_parent=52e9, eps_basic=41.76),
    ]
    balance = [
        _bs("2019", total_assets=160e9, total_liabilities=35e9,
            shareholders_equity=125e9, short_term_debt=0, long_term_debt=0,
            cash_and_equivalents=90e9),
        _bs("2020", total_assets=190e9, total_liabilities=42e9,
            shareholders_equity=148e9, short_term_debt=0, long_term_debt=0,
            cash_and_equivalents=110e9),
        _bs("2021", total_assets=220e9, total_liabilities=50e9,
            shareholders_equity=170e9, short_term_debt=0, long_term_debt=0,
            cash_and_equivalents=130e9),
    ]
    cash_flow = [
        _cf("2019", operating_cash_flow=45e9, capex=3e9, free_cash_flow=42e9,
            dividends_paid=-20e9),
        _cf("2020", operating_cash_flow=50e9, capex=3.5e9, free_cash_flow=46.5e9,
            dividends_paid=-24e9),
        _cf("2021", operating_cash_flow=55e9, capex=4e9, free_cash_flow=51e9,
            dividends_paid=-27e9),
    ]
    return income, balance, cash_flow


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMaotaiLike:
    def test_fiscal_years(self):
        r = compute_ratios(*_maotai_data())
        assert r["fiscal_years"] == ["2019", "2020", "2021"]

    def test_roe(self):
        r = compute_ratios(*_maotai_data())
        # 41e9/125e9 = 0.328, 47e9/148e9 = 0.3176, 52e9/170e9 = 0.3059
        assert len(r["roe"]) == 3
        assert r["roe"][0] == pytest.approx(0.328, abs=0.001)
        assert r["roe"][2] == pytest.approx(0.3059, abs=0.001)

    def test_gross_margin(self):
        r = compute_ratios(*_maotai_data())
        # (88e9 - 8e9) / 88e9 = 0.909
        assert r["gross_margin"][0] == pytest.approx(0.909, abs=0.001)

    def test_net_margin(self):
        r = compute_ratios(*_maotai_data())
        assert r["net_margin"][0] == pytest.approx(41e9 / 88e9, abs=0.001)

    def test_revenue_growth(self):
        r = compute_ratios(*_maotai_data())
        assert r["revenue_growth"][0] is None  # first year
        assert r["revenue_growth"][1] == pytest.approx((95e9 - 88e9) / 88e9, abs=0.001)

    def test_ocf_to_ni(self):
        r = compute_ratios(*_maotai_data())
        assert r["ocf_to_ni"][0] == pytest.approx(45e9 / 41e9, abs=0.01)

    def test_debt_to_assets(self):
        r = compute_ratios(*_maotai_data())
        assert r["debt_to_assets"][0] == pytest.approx(35e9 / 160e9, abs=0.001)

    def test_net_debt_negative_means_net_cash(self):
        r = compute_ratios(*_maotai_data())
        # no debt, 90e9 cash: net_debt = -90e9, ebit = 59e9 → negative ratio
        assert r["net_debt_to_ebit"][0] is not None
        assert r["net_debt_to_ebit"][0] < 0

    def test_interest_coverage_no_interest(self):
        r = compute_ratios(*_maotai_data())
        # interest_expense = 0 → None (division by zero)
        assert r["interest_coverage"][0] is None


class TestPreferNetIncomeToParent:
    def test_uses_parent_when_available(self):
        inc = [_is("2023", revenue=100, net_income=80, net_income_to_parent=75)]
        bal = [_bs("2023", shareholders_equity=500)]
        r = compute_ratios(inc, bal, [])
        assert r["roe"][0] == pytest.approx(75 / 500)

    def test_falls_back_to_net_income(self):
        inc = [_is("2023", revenue=100, net_income=80)]
        bal = [_bs("2023", shareholders_equity=500)]
        r = compute_ratios(inc, bal, [])
        assert r["roe"][0] == pytest.approx(80 / 500)


class TestGrowthRates:
    def test_first_year_is_none(self):
        inc = [
            _is("2022", revenue=100, net_income=10, eps_basic=1.0),
            _is("2023", revenue=120, net_income=15, eps_basic=1.5),
        ]
        r = compute_ratios(inc, [], [])
        assert r["revenue_growth"][0] is None
        assert r["revenue_growth"][1] == pytest.approx(0.2)
        assert r["eps_growth"][1] == pytest.approx(0.5)

    def test_growth_from_zero_is_none(self):
        inc = [
            _is("2022", revenue=0, net_income=0, eps_basic=0),
            _is("2023", revenue=100, net_income=10, eps_basic=1.0),
        ]
        r = compute_ratios(inc, [], [])
        assert r["revenue_growth"][1] is None


class TestNonePropagation:
    def test_scattered_nones(self):
        inc = [_is("2023", revenue=None, net_income=None)]
        bal = [_bs("2023", shareholders_equity=None)]
        cf = [_cf("2023", operating_cash_flow=None)]
        r = compute_ratios(inc, bal, cf)
        assert r["roe"][0] is None
        assert r["gross_margin"][0] is None
        assert r["net_margin"][0] is None
        assert r["ocf_to_ni"][0] is None


class TestZeroDenominator:
    def test_zero_equity(self):
        inc = [_is("2023", net_income=10)]
        bal = [_bs("2023", shareholders_equity=0)]
        r = compute_ratios(inc, bal, [])
        assert r["roe"][0] is None

    def test_zero_revenue(self):
        inc = [_is("2023", revenue=0, gross_profit=0)]
        r = compute_ratios(inc, [], [])
        assert r["gross_margin"][0] is None
        assert r["net_margin"][0] is None


class TestEmptyInput:
    def test_empty_lists(self):
        r = compute_ratios([], [], [])
        assert r["fiscal_years"] == []
        for k, v in r.items():
            assert isinstance(v, list)
            assert len(v) == 0


class TestSingleYear:
    def test_profitability_works_growth_is_none(self):
        inc = [_is("2023", revenue=100, cost_of_revenue=30, net_income=20)]
        bal = [_bs("2023", shareholders_equity=200)]
        r = compute_ratios(inc, bal, [])
        assert r["roe"][0] == pytest.approx(0.1)
        assert r["gross_margin"][0] == pytest.approx(0.7)
        assert r["revenue_growth"][0] is None


class TestMismatchedYears:
    def test_partial_coverage(self):
        inc = [_is("2021", revenue=100, net_income=10), _is("2022", revenue=120, net_income=15)]
        bal = [_bs("2022", shareholders_equity=200)]
        cf = [_cf("2021", operating_cash_flow=12)]
        r = compute_ratios(inc, bal, cf)
        assert r["fiscal_years"] == ["2021", "2022"]
        # 2021: has income + cash_flow but no balance
        assert r["roe"][0] is None  # no equity for 2021
        assert r["ocf_to_ni"][0] == pytest.approx(12 / 10)
        # 2022: has income + balance but no cash_flow
        assert r["roe"][1] == pytest.approx(15 / 200)
        assert r["ocf_to_ni"][1] is None


class TestValuation:
    def test_with_market_cap(self):
        inc = [_is("2023", net_income=10)]
        bal = [_bs("2023", shareholders_equity=200)]
        cf = [_cf("2023", dividends_paid=-5)]
        r = compute_ratios(inc, bal, cf, market_cap=1000)
        assert r["pe_ttm"][0] == pytest.approx(100)
        assert r["pb"][0] == pytest.approx(5)
        assert r["dividend_yield"][0] == pytest.approx(0.005)

    def test_without_market_cap(self):
        inc = [_is("2023", net_income=10)]
        bal = [_bs("2023", shareholders_equity=200)]
        r = compute_ratios(inc, bal, [])
        assert r["pe_ttm"][0] is None
        assert r["pb"][0] is None
        assert r["dividend_yield"][0] is None


class TestLeverageNetCash:
    def test_net_cash_company(self):
        inc = [_is("2023", operating_income=50)]
        bal = [_bs("2023", short_term_debt=10, long_term_debt=0, cash_and_equivalents=100)]
        r = compute_ratios(inc, bal, [])
        # net_debt = 10 - 100 = -90, ebit = 50 → -1.8
        assert r["net_debt_to_ebit"][0] == pytest.approx(-1.8)


class TestGrossMarginDerived:
    def test_derived_from_revenue_minus_cost(self):
        """gross_profit is None but revenue and cost_of_revenue are present."""
        inc = [_is("2023", revenue=100, cost_of_revenue=40)]
        r = compute_ratios(inc, [], [])
        assert r["gross_margin"][0] == pytest.approx(0.6)

    def test_explicit_gross_profit_preferred(self):
        inc = [_is("2023", revenue=100, cost_of_revenue=40, gross_profit=65)]
        r = compute_ratios(inc, [], [])
        assert r["gross_margin"][0] == pytest.approx(0.65)
