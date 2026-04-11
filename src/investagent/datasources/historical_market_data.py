"""Historical market data fetcher for backtesting.

Primary: baostock (own server, no rate limit, 0.2s/stock, includes PE/PB)
Fallback: AkShare Sina source (price only)
No AkShare/同花顺 dependency for A-shares — avoids Semaphore(1) bottleneck.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, timedelta
from typing import Any

from investagent.datasources.base import MarketDataFetcher, MarketQuote
from investagent.datasources.resolver import _YFINANCE_SUFFIX

logger = logging.getLogger(__name__)

_BS_LOGGED_IN = False


_BS_LOGIN_LOCK = threading.Lock()


def _ensure_baostock_login() -> None:
    global _BS_LOGGED_IN
    if _BS_LOGGED_IN:
        return
    with _BS_LOGIN_LOCK:
        if _BS_LOGGED_IN:  # double-check after acquiring lock
            return
        import baostock as bs
        logger.info("baostock: logging in to %s:%s...", "www.baostock.com", 10030)
        lg = bs.login()
        logger.info("baostock: login result: code=%s msg=%s", lg.error_code, lg.error_msg)
        _BS_LOGGED_IN = True
        # baostock uses raw TCP sockets with no timeout — socket.recv()
        # blocks forever if the server hangs. Set a 30s timeout on the
        # global socket to prevent deadlocks.
        try:
            import baostock.common.context as bs_ctx
            sock = getattr(bs_ctx, "default_socket", None)
            if sock is not None:
                sock.settimeout(30)
                logger.info("baostock: socket timeout set to 30s")
            else:
                logger.warning("baostock: no socket found after login")
        except Exception:
            logger.warning("baostock: failed to set socket timeout", exc_info=True)


def _fetch_quote_baostock(ticker: str, exchange: str, as_of_date: date) -> dict[str, Any] | None:
    """Get close + PE + PB from baostock in one call. No AkShare dependency."""
    import baostock as bs
    import time as _time

    _ensure_baostock_login()

    code = ticker.split(".")[0].zfill(6)
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    bs_code = f"{prefix}.{code}"

    start = (as_of_date - timedelta(days=15)).strftime("%Y-%m-%d")
    end = as_of_date.strftime("%Y-%m-%d")

    try:
        t0 = _time.time()
        rs = bs.query_history_k_data_plus(
            bs_code, "date,close,peTTM,pbMRQ",
            start_date=start, end_date=end,
            frequency="d", adjustflag="2",
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        elapsed = _time.time() - t0
        if elapsed > 5:
            logger.warning("baostock SLOW query for %s: %.1fs", ticker, elapsed)
        if rows:
            last = rows[-1]
            close = float(last[1]) if last[1] else None
            pe = float(last[2]) if last[2] else None
            pb = float(last[3]) if last[3] else None
            logger.debug("baostock %s: close=%s pe=%s pb=%s (%.1fs)", ticker, close, pe, pb, elapsed)
            return {"close": close, "pe": pe, "pb": pb}
        else:
            logger.warning("baostock %s: no data returned (error_code=%s, %.1fs)", ticker, rs.error_code, elapsed)
    except Exception:
        logger.warning("baostock failed for %s", ticker, exc_info=True)
    return None


def _fetch_price_sina(ticker: str, exchange: str, as_of_date: date) -> float | None:
    """Fallback: AkShare Sina source for close price only."""
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
    """Fetch historical quote as of a specific date.

    A-shares: baostock gives close + PE(TTM) + PB in ONE call (0.2s).
    No 同花顺/AkShare calls needed — no Semaphore bottleneck.
    """
    import re
    code = re.sub(r"[^\d]", "", ticker.split(".")[0]).zfill(6)

    currency_map = {"SSE": "CNY", "SZSE": "CNY", "BSE": "CNY",
                    "上交所": "CNY", "深交所": "CNY", "北交所": "CNY",
                    "HKEX": "HKD", "港交所": "HKD"}
    currency = currency_map.get(exchange, "USD")

    price = None
    pe_ratio = None
    pb_ratio = None
    market_cap = None
    shares = None

    if currency == "CNY":
        # Primary: baostock (price + PE + PB, no AkShare dependency)
        quote = _fetch_quote_baostock(ticker, exchange, as_of_date)
        if quote and quote.get("close"):
            price = quote["close"]
            pe_ratio = quote.get("pe")
            pb_ratio = quote.get("pb")

        # Fallback: Sina (price only)
        if price is None:
            price = _fetch_price_sina(ticker, exchange, as_of_date)

    else:
        # HK/US: yfinance
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
                pe_ratio = info.get("trailingPE")
                pb_ratio = info.get("priceToBook")
        except Exception:
            logger.warning("yfinance historical failed for %s", ticker, exc_info=True)

    return MarketQuote(
        ticker=ticker,
        name=ticker,
        currency=currency,
        price=price,
        market_cap=market_cap,
        pe_ratio=pe_ratio,
        pb_ratio=pb_ratio,
        shares_outstanding=shares,
    )


class HistoricalMarketDataFetcher(MarketDataFetcher):
    """Fetch historical market data as of a specific date.

    baostock does NOT use AkShare — no Semaphore(1) contention.
    """

    def __init__(self, as_of_date: date, exchange: str = "SSE") -> None:
        self._as_of_date = as_of_date
        self._exchange = exchange

    async def get_quote(self, ticker: str) -> MarketQuote:
        # baostock is thread-safe (TCP socket, not V8) — no AkShare lock needed
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
