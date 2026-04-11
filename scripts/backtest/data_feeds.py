"""Historical data fetching for backtesting.

Primary: baostock (own server, no rate limit, ~250ms/stock)
Fallback: AkShare Sina source
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)


def _fetch_daily_baostock(code: str, start: date, end: date) -> pd.DataFrame | None:
    """Fetch daily OHLCV from baostock."""
    try:
        import baostock as bs
        from investagent.datasources.historical_market_data import _ensure_baostock_login
        _ensure_baostock_login()

        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        bs_code = f"{prefix}.{code}"

        rs = bs.query_history_k_data_plus(
            bs_code, "date,open,high,low,close,volume",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            frequency="d", adjustflag="2",  # forward-adjusted
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        logger.debug("baostock daily failed for %s", code, exc_info=True)
        return None


def _fetch_daily_sina(code: str, start: date, end: date) -> pd.DataFrame | None:
    """Fallback: AkShare Sina source."""
    try:
        import akshare as ak
        from investagent.datasources.akshare_source import _akshare_call_with_retry

        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        df = _akshare_call_with_retry(
            ak.stock_zh_a_daily,
            f"{prefix}{code}",
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            "qfq",
            label=f"daily Sina {code}",
        )
        if df.empty:
            return None
        df = df.rename(columns={
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
        })
        df["date"] = pd.to_datetime(df["date"])
        return df[["date", "open", "high", "low", "close", "volume"]]
    except Exception:
        logger.debug("Sina daily failed for %s", code, exc_info=True)
        return None


def fetch_daily_prices(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Fetch daily OHLCV for an A-share stock.

    Primary: baostock (own server, no rate limit)
    Fallback: AkShare Sina

    Returns DataFrame with columns: date, open, high, low, close, volume.
    """
    code = ticker.split(".")[0].zfill(6)

    df = _fetch_daily_baostock(code, start, end)
    if df is not None and not df.empty:
        return df

    df = _fetch_daily_sina(code, start, end)
    if df is not None and not df.empty:
        return df

    logger.warning("All daily price sources failed for %s", ticker)
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def fetch_benchmark(index_code: str, start: date, end: date) -> pd.DataFrame:
    """Fetch daily index data for benchmarking.

    index_code: "000300" (CSI 300), "HSI" (Hang Seng), "SPX" (S&P 500)

    CSI 300 uses baostock (no rate limit); others use AkShare.
    """
    # CSI 300: baostock
    if index_code == "000300":
        try:
            import baostock as bs
            from investagent.datasources.historical_market_data import _ensure_baostock_login
            _ensure_baostock_login()

            rs = bs.query_history_k_data_plus(
                "sh.000300", "date,close",
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                frequency="d",
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if rows:
                df = pd.DataFrame(rows, columns=["date", "close"])
                df["date"] = pd.to_datetime(df["date"])
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                return df
        except Exception:
            logger.warning("baostock benchmark failed for CSI300", exc_info=True)

    # Other indices / fallback: AkShare
    import akshare as ak
    try:
        if index_code == "000300":
            df = ak.stock_zh_index_daily(symbol="sh000300")
        elif index_code == "HSI":
            df = ak.stock_hk_index_daily_em(symbol="HSI")
        elif index_code == "SPX":
            df = ak.index_us_stock_sina(symbol=".INX")
        else:
            raise ValueError(f"Unknown index: {index_code}")

        df = df.rename(columns={"date": "date", "close": "close"})
        if "date" not in df.columns:
            for col in df.columns:
                if "日期" in str(col):
                    df = df.rename(columns={col: "date"})
                if "收盘" in str(col):
                    df = df.rename(columns={col: "close"})

        df["date"] = pd.to_datetime(df["date"])
        mask = (df["date"].dt.date >= start) & (df["date"].dt.date <= end)
        return df.loc[mask, ["date", "close"]].reset_index(drop=True)
    except Exception:
        logger.warning("Failed to fetch benchmark %s", index_code, exc_info=True)
        return pd.DataFrame(columns=["date", "close"])


def fetch_risk_free_rate(year: int) -> float:
    """Return approximate 1-year Chinese government bond yield for a given year.

    Used for cash return calculation in backtesting.
    """
    # Historical 1-year CGBs (approximate annual averages)
    rates = {
        2023: 0.0210,
        2024: 0.0160,
        2025: 0.0140,
    }
    return rates.get(year, 0.0200)
