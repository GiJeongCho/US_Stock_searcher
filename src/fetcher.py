"""
yfinance 데이터 fetcher - 캐싱 + 속도 제한
"""
import time
import logging
import threading
import yfinance as yf
import pandas as pd

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

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

# 인터벌별 필요 기간 (Yahoo 제한: 1m=7d, 5m/15m=59d, 60m/1h=730d)
PERIOD_MAP = {
    "1m":  "5d",
    "5m":  "55d",
    "15m": "55d",
    "60m": "60d",
    "1h":  "60d",
    "1d":  "1y",
}

# API 호출 간격 제어 (마지막 호출 시간)
_last_call_time = 0
_MIN_CALL_INTERVAL = 0.3
_MAX_RETRIES = 3
_BACKOFF_BASE = 10  # 초 (Rate Limit 시 대기 기본값)


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

    for attempt in range(_MAX_RETRIES):
        _rate_limit()
        try:
            period = PERIOD_MAP.get(interval, "60d")
            df = yf.download(
                ticker,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=True,
                prepost=True,
                threads=False,
            )
            if df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.dropna()
            with _lock:
                _cache[key] = (time.time(), df)
            return df
        except Exception as e:
            err_msg = str(e).lower()
            if "too many requests" in err_msg or "rate limit" in err_msg:
                wait = _BACKOFF_BASE * (2 ** attempt)
                logging.warning(f"[fetcher] {ticker} {interval} Rate Limited → {wait}초 대기 ({attempt+1}/{_MAX_RETRIES})")
                time.sleep(wait)
            else:
                logging.warning(f"[fetcher] {ticker} {interval} 오류: {e}")
                return None

    logging.warning(f"[fetcher] {ticker} {interval} 최종 실패 (Rate Limit)")
    return None


def get_info(ticker: str) -> dict:
    """종목 기본정보 (시총, 유통주식수 등) - TTL 10분, Rate Limit 시 자동 백오프"""
    key = (ticker.upper(), "info")
    ttl = 600

    with _lock:
        if key in _cache:
            ts, info = _cache[key]
            if time.time() - ts < ttl:
                return info

    for attempt in range(_MAX_RETRIES):
        _rate_limit()
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            with _lock:
                _cache[key] = (time.time(), info)
            return info
        except Exception as e:
            err_msg = str(e).lower()
            if "too many requests" in err_msg or "rate limit" in err_msg:
                wait = _BACKOFF_BASE * (2 ** attempt)
                logging.warning(f"[fetcher] {ticker} Rate Limited → {wait}초 대기 후 재시도 ({attempt+1}/{_MAX_RETRIES})")
                time.sleep(wait)
            else:
                logging.warning(f"[fetcher] {ticker} info 오류: {e}")
                return {}

    logging.warning(f"[fetcher] {ticker} info 최종 실패 (Rate Limit)")
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
