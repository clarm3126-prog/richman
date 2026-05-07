#!/usr/bin/env python3
"""KOSPI + KOSDAQ 전 종목 시세를 수집해 data/market.json으로 저장."""
import json
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pytz

KST = pytz.timezone("Asia/Seoul")


def get_latest_trading_day(stock):
    today = datetime.now(KST).date()
    for offset in range(0, 14):
        candidate = today - timedelta(days=offset)
        day_str = candidate.strftime("%Y%m%d")
        try:
            df = stock.get_market_ohlcv_by_ticker(day_str, "KOSPI")
            if df is not None and not df.empty and df["거래량"].sum() > 0:
                return day_str
        except Exception:
            continue
    raise RuntimeError("최근 영업일을 찾지 못했습니다")


def get_name_map():
    """code -> {name, market} 매핑을 FinanceDataReader로 일괄 조회."""
    import FinanceDataReader as fdr
    listing = fdr.StockListing("KRX")
    code_col = "Code" if "Code" in listing.columns else "Symbol"
    name_col = "Name"
    market_col = "Market" if "Market" in listing.columns else None
    out = {}
    for _, row in listing.iterrows():
        code = str(row[code_col]).zfill(6)
        if not code.isdigit():
            continue
        name = row.get(name_col)
        if not name:
            continue
        out[code] = {
            "name": str(name),
            "market": str(row[market_col]) if market_col and market_col in listing.columns else "",
        }
    return out


def fetch_market_prices(stock, day_str, market_name):
    df = stock.get_market_ohlcv_by_ticker(day_str, market_name)
    if df is None or df.empty:
        return {}
    out = {}
    for code, row in df.iterrows():
        try:
            out[str(code).zfill(6)] = {
                "price": int(row["종가"]),
                "change": float(row["등락률"]),
                "volume": int(row["거래량"]),
            }
        except Exception:
            continue
    return out


def fetch_index(stock, day_str, ticker):
    try:
        df = stock.get_index_ohlcv(day_str, day_str, ticker)
        if df.empty:
            return None
        row = df.iloc[-1]
        return {
            "value": float(row["종가"]),
            "change": float(row["등락률"]) if "등락률" in row else 0.0,
        }
    except Exception:
        return None


def main():
    from pykrx import stock

    day_str = get_latest_trading_day(stock)
    print(f"Trading day: {day_str}")

    name_map = get_name_map()
    print(f"Names loaded: {len(name_map)}")

    stocks = {}
    for market_name in ["KOSPI", "KOSDAQ"]:
        prices = fetch_market_prices(stock, day_str, market_name)
        for code, pdata in prices.items():
            info = name_map.get(code, {})
            stocks[code] = {
                "name": info.get("name", code),
                "market": market_name,
                **pdata,
            }
        print(f"{market_name}: {len(prices)} stocks")

    indices = {
        "kospi": fetch_index(stock, day_str, "1001"),
        "kosdaq": fetch_index(stock, day_str, "2001"),
    }

    out = {
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "trading_day": day_str,
        "indices": indices,
        "stocks": stocks,
    }

    out_path = Path("data/market.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {out_path}: {out_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
