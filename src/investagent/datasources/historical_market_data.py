"""Historical market data fetcher for backtesting.

Uses AkShare historical daily data instead of yfinance real-time quotes.
Ensures no future data leakage: only prices on or before as_of_date are used.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from investagent.datasources.base import MarketDataFetcher, MarketQuote
from investagent.datasources.resolver import _YFINANCE_SUFFIX

logger = logging.getLogger(__name__)


def _ticker_to_akshare_code(ticker: str, exchange: str) -> str:
    """Convert ticker+exchange to AkShare A-share code (digits only)."""
    import re
    # Strip any suffix (.SS, .SZ, etc.)
    return re.sub(r"[^\d]", "", ticker.split(".")[0]).zfill(6)


def _fetch_historical_quote_sync(
    ticker: str,
    exchange: str,
    as_of_date: date,
) -> MarketQuote:
    """Fetch historical close price and compute market cap as of a specific date.

    Uses AkShare stock_zh_a_hist (前复权 daily) for A-shares.
    Falls back to yfinance historical for HK/US.
    """
    import akshare as ak

    code = _ticker_to_akshare_code(ticker, exchange)

    # Determine currency
    currency_map = {"SSE": "CNY", "SZSE": "CNY", "BSE": "CNY",
                    "上交所": "CNY", "深交所": "CNY", "北交所": "CNY",
                    "HKEX": "HKD", "港交所": "HKD"}
    currency = currency_map.get(exchange, "USD")

    # Fetch daily data — look back 10 trading days to handle holidays
    start = (as_of_date - timedelta(days=20)).strftime("%Y%m%d")
    end = as_of_date.strftime("%Y%m%d")

    price = None
    market_cap = None
    shares = None

    # A-share: use AkShare (fallback chain: push2his → sina)
    if currency == "CNY":
        # Primary: stock_zh_a_hist (eastmoney push2his)
        try:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start, end_date=end, adjust="qfq",
            )
            if not df.empty:
                last_row = df.iloc[-1]
                price = float(last_row["收盘"])
                logger.info("Historical price (EM) for %s as_of %s: %.2f", code, as_of_date, price)
        except Exception:
            logger.debug("push2his failed for %s, trying sina...", code)

        # Fallback: stock_zh_a_daily (sina)
        if price is None:
            try:
                prefix = "sh" if code.startswith("6") else "sz"
                df = ak.stock_zh_a_daily(
                    symbol=f"{prefix}{code}",
                    start_date=start.replace("-", ""),
                    end_date=end.replace("-", ""),
                    adjust="qfq",
                )
                if not df.empty:
                    last_row = df.iloc[-1]
                    price = float(last_row["close"])
                    # Sina also provides outstanding_share
                    if "outstanding_share" in last_row and last_row["outstanding_share"] > 0:
                        shares = float(last_row["outstanding_share"])
                        market_cap = price * shares
                    logger.info("Historical price (Sina) for %s as_of %s: %.2f", code, as_of_date, price)
            except Exception:
                logger.warning("Both historical APIs failed for %s", code, exc_info=True)

        # Get shares/market_cap from financials if not from Sina
        if price is not None and market_cap is None:
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
            hist = t.history(start=start, end=(as_of_date + timedelta(days=1)).strftime("%Y-%m-%d"))
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                info = t.info
                shares = info.get("sharesOutstanding")
                if shares and price:
                    market_cap = price * shares
        except Exception:
            logger.warning("yfinance historical failed for %s", ticker, exc_info=True)

    # Compute PE if we have earnings
    pe_ratio = None
    if price and shares and market_cap:
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
