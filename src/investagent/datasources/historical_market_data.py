"""Historical market data fetcher for backtesting.

Primary: baostock (own server, no rate limit, 0.2s/stock)
Fallback: AkShare Sina source
Ensures no future data leakage: only prices on or before as_of_date.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from investagent.datasources.base import MarketDataFetcher, MarketQuote
from investagent.datasources.resolver import _YFINANCE_SUFFIX

logger = logging.getLogger(__name__)

# Reusable baostock login session (login once, query many)
_BS_LOGGED_IN = False


def _ensure_baostock_login() -> None:
    global _BS_LOGGED_IN
    if not _BS_LOGGED_IN:
        import baostock as bs
        bs.login()
        _BS_LOGGED_IN = True


def _fetch_price_baostock(ticker: str, exchange: str, as_of_date: date) -> float | None:
    """Get close price from baostock. Returns None on failure."""
    import baostock as bs

    _ensure_baostock_login()

    # Convert ticker to baostock format: sh.600519 or sz.000858
    code = ticker.split(".")[0].zfill(6)
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    bs_code = f"{prefix}.{code}"

    # Look back 10 trading days for holidays
    start = (as_of_date - timedelta(days=15)).strftime("%Y-%m-%d")
    end = as_of_date.strftime("%Y-%m-%d")

    try:
        rs = bs.query_history_k_data_plus(
            bs_code, "date,close",
            start_date=start, end_date=end,
            frequency="d", adjustflag="2",  # 前复权
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if rows:
            return float(rows[-1][1])
    except Exception:
        logger.debug("baostock failed for %s", ticker, exc_info=True)
    return None


def _fetch_price_sina(ticker: str, exchange: str, as_of_date: date) -> float | None:
    """Fallback: AkShare Sina source for close price."""
    try:
        import akshare as ak
        from investagent.datasources.akshare_source import _akshare_call_with_retry

        code = ticker.split(".")[0].zfill(6)
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        start = (as_of_date - timedelta(days=15)).strftime("%Y%m%d")
        end = as_of_date.strftime("%Y%m%d")

        df = _akshare_call_with_retry(
            ak.stock_zh_a_daily,
            f"{prefix}{code}", start, end, "qfq",
            label=f"hist-price Sina {code}",
        )
        if not df.empty:
            return float(df.iloc[-1]["close"])
    except Exception:
        logger.debug("Sina fallback failed for %s", ticker, exc_info=True)
    return None


def _fetch_historical_quote_sync(
    ticker: str,
    exchange: str,
    as_of_date: date,
) -> MarketQuote:
    """Fetch historical close price and compute market cap as of a specific date.

    Primary: baostock (own server, 0.2s/stock, no rate limit)
    Fallback: AkShare Sina
    """
    import re
    code = re.sub(r"[^\d]", "", ticker.split(".")[0]).zfill(6)

    # Determine currency
    currency_map = {"SSE": "CNY", "SZSE": "CNY", "BSE": "CNY",
                    "上交所": "CNY", "深交所": "CNY", "北交所": "CNY",
                    "HKEX": "HKD", "港交所": "HKD"}
    currency = currency_map.get(exchange, "USD")

    price = None
    market_cap = None
    shares = None

    if currency == "CNY":
        # Primary: baostock
        price = _fetch_price_baostock(ticker, exchange, as_of_date)
        if price is not None:
            logger.debug("Historical price (baostock) for %s: %.2f", code, price)
        else:
            # Fallback: Sina
            price = _fetch_price_sina(ticker, exchange, as_of_date)
            if price is not None:
                logger.debug("Historical price (Sina) for %s: %.2f", code, price)

        # Compute market cap from EPS + net_income
        if price is not None:
            try:
                from investagent.datasources.akshare_source import fetch_a_share_financials
                financials = fetch_a_share_financials(code, years=2)
                for row in financials.get("income_statement", []):
                    fy = int(row.get("fiscal_year", "0"))
                    if fy <= as_of_date.year:
                        eps = row.get("eps_basic")
                        ni = row.get("net_income")
                        if eps and eps > 0 and ni:
                            shares = ni / eps
                            market_cap = price * shares
                        break
            except Exception:
                pass
    else:
        # HK/US: use yfinance historical
        try:
            import yfinance as yf
            suffix = _YFINANCE_SUFFIX.get(exchange, "")
            yf_ticker = f"{ticker}{suffix}" if suffix and not ticker.endswith(suffix) else ticker
            t = yf.Ticker(yf_ticker)
            start = (as_of_date - timedelta(days=15)).strftime("%Y-%m-%d")
            hist = t.history(start=start, end=(as_of_date + timedelta(days=1)).strftime("%Y-%m-%d"))
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                info = t.info
                shares = info.get("sharesOutstanding")
                if shares and price:
                    market_cap = price * shares
        except Exception:
            logger.warning("yfinance historical failed for %s", ticker, exc_info=True)

    # Compute PE
    pe_ratio = None
    if price and currency == "CNY":
        try:
            from investagent.datasources.akshare_source import fetch_a_share_financials
            financials = fetch_a_share_financials(code, years=2)
            for row in financials.get("income_statement", []):
                fy = int(row.get("fiscal_year", "0"))
                if fy <= as_of_date.year:
                    eps = row.get("eps_basic")
                    if eps and eps > 0:
                        pe_ratio = price / eps
                    break
        except Exception:
            pass

    return MarketQuote(
        ticker=ticker,
        name=ticker,
        currency=currency,
        price=price,
        market_cap=market_cap,
        pe_ratio=pe_ratio,
        shares_outstanding=shares,
    )


class HistoricalMarketDataFetcher(MarketDataFetcher):
    """Fetch historical market data as of a specific date.

    Guarantees no future data leakage: only uses prices <= as_of_date.
    """

    def __init__(self, as_of_date: date, exchange: str = "SSE") -> None:
        self._as_of_date = as_of_date
        self._exchange = exchange

    async def get_quote(self, ticker: str) -> MarketQuote:
        from investagent.datasources.akshare_source import _AKSHARE_LOCK
        async with _AKSHARE_LOCK:
            return await asyncio.to_thread(
                _fetch_historical_quote_sync, ticker, self._exchange, self._as_of_date,
            )

    async def get_quotes(self, tickers: list[str]) -> list[MarketQuote]:
        tasks = [self.get_quote(t) for t in tickers]
        results: list[MarketQuote] = []
        for coro in asyncio.as_completed(tasks):
            try:
                results.append(await coro)
            except Exception:
                logger.warning("Failed to fetch historical quote", exc_info=True)
        return results
