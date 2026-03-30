"""Historical data fetching for backtesting via AkShare."""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)


def fetch_daily_prices(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Fetch daily OHLCV for an A-share stock.

    Returns DataFrame with columns: date, open, high, low, close, volume.
    """
    import akshare as ak

    code = ticker.split(".")[0].zfill(6)
    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",  # forward-adjusted
        )
        df = df.rename(columns={
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
        })
        df["date"] = pd.to_datetime(df["date"])
        return df[["date", "open", "high", "low", "close", "volume"]]
    except Exception:
        logger.warning("Failed to fetch prices for %s", ticker, exc_info=True)
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def fetch_benchmark(index_code: str, start: date, end: date) -> pd.DataFrame:
    """Fetch daily index data for benchmarking.

    index_code: "000300" (CSI 300), "HSI" (Hang Seng), "SPX" (S&P 500)
    """
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

        df = df.rename(columns={
            "date": "date",
            "close": "close",
        })
        if "date" not in df.columns:
            # Some AkShare APIs use Chinese column names
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
