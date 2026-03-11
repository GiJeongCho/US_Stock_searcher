"""
yfinance 데이터 fetcher - 캐싱 + 속도 제한
"""
import time
import threading
import yfinance as yf
import pandas as pd

# 캐시: { (ticker, interval): (timestamp, DataFrame) }
_cache: dict = {}
_lock = threading.Lock()

# 인터벌별 캐시 유효시간 (초)
CACHE_TTL = {
    "1m":  30,
    "5m":  120,
    "15m": 300,
    "60m": 600,
    "1h":  600,
    "1d":  600,
}

# 인터벌별 필요 기간
PERIOD_MAP = {
    "1m":  "7d",
    "5m":  "60d",
    "15m": "60d",
    "60m": "60d",
    "1h":  "60d",
    "1d":  "1y",
}

# API 호출 간격 제어 (마지막 호출 시간)
_last_call_time = 0
_MIN_CALL_INTERVAL = 0.5  # 초


def _rate_limit():
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)
    _last_call_time = time.time()


def get_ohlcv(ticker: str, interval: str) -> pd.DataFrame | None:
    """단일 종목 OHLCV 반환 (캐시 우선)"""
    key = (ticker.upper(), interval)
    ttl = CACHE_TTL.get(interval, 120)

    with _lock:
        if key in _cache:
            ts, df = _cache[key]
            if time.time() - ts < ttl:
                return df

    _rate_limit()
    try:
        period = PERIOD_MAP.get(interval, "60d")
        df = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
        if df.empty:
            return None
        # 컬럼이 멀티인덱스인 경우 평탄화
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        with _lock:
            _cache[key] = (time.time(), df)
        return df
    except Exception as e:
        print(f"[fetcher] {ticker} {interval} 오류: {e}")
        return None


def get_info(ticker: str) -> dict:
    """종목 기본정보 (시총, 유통주식수 등) - TTL 10분"""
    key = (ticker.upper(), "info")
    ttl = 600

    with _lock:
        if key in _cache:
            ts, info = _cache[key]
            if time.time() - ts < ttl:
                return info

    _rate_limit()
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        with _lock:
            _cache[key] = (time.time(), info)
        return info
    except Exception as e:
        print(f"[fetcher] {ticker} info 오류: {e}")
        return {}


def clear_cache(ticker: str | None = None):
    """캐시 초기화 (ticker=None 이면 전체)"""
    with _lock:
        if ticker is None:
            _cache.clear()
        else:
            keys = [k for k in _cache if k[0] == ticker.upper()]
            for k in keys:
                del _cache[k]
