"""
기술적 지표 계산
"""
import pandas as pd
import numpy as np


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def bollinger_bands(close: pd.Series, period: int = 20, std: float = 2.0):
    """(upper, mid, lower) 반환"""
    mid = sma(close, period)
    sigma = close.rolling(window=period).std(ddof=0)
    upper = mid + std * sigma
    lower = mid - std * sigma
    return upper, mid, lower


def envelope(close: pd.Series, period: int = 12, pct: float = 2.2):
    """(upper, mid, lower) 반환"""
    mid = sma(close, period)
    upper = mid * (1 + pct / 100)
    lower = mid * (1 - pct / 100)
    return upper, mid, lower


# ── 조건 평가 함수들 ─────────────────────────────────────────

def check_ma_alignment(df: pd.DataFrame, periods: list[int]) -> bool:
    """MA_a >= MA_b >= MA_c (현재 봉 기준)"""
    close = df["Close"]
    mas = []
    for p in periods:
        if len(close) < p:
            return False
        mas.append(sma(close, p).iloc[-1])
    return all(mas[i] >= mas[i + 1] for i in range(len(mas) - 1))


def check_bb_breakout(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> bool:
    """종가가 볼린저 상한선 상향돌파 (전봉 이하 → 현봉 이상)"""
    close = df["Close"]
    if len(close) < period + 1:
        return False
    upper, _, _ = bollinger_bands(close, period, std)
    return close.iloc[-2] < upper.iloc[-2] and close.iloc[-1] >= upper.iloc[-1]


def check_bb_above(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> bool:
    """종가가 볼린저 상한선 이상"""
    close = df["Close"]
    if len(close) < period:
        return False
    upper, _, _ = bollinger_bands(close, period, std)
    return close.iloc[-1] >= upper.iloc[-1]


def check_envelope_breakout(df: pd.DataFrame, period: int = 12, pct: float = 2.2) -> bool:
    """종가가 엔벨로프 상한선 상향돌파 (전봉 이하 → 현봉 이상)"""
    close = df["Close"]
    if len(close) < period + 1:
        return False
    upper, _, _ = envelope(close, period, pct)
    return close.iloc[-2] < upper.iloc[-2] and close.iloc[-1] >= upper.iloc[-1]


def check_ma_gap(df: pd.DataFrame, fast: int, slow: int, threshold_pct: float) -> bool:
    """이평이격도: |MA_fast - MA_slow| / MA_slow <= threshold_pct%"""
    close = df["Close"]
    needed = max(fast, slow)
    if len(close) < needed:
        return False
    ma_fast = sma(close, fast).iloc[-1]
    ma_slow = sma(close, slow).iloc[-1]
    if ma_slow == 0:
        return False
    gap_pct = abs(ma_fast - ma_slow) / ma_slow * 100
    return gap_pct <= threshold_pct


def check_ma_compare(df: pd.DataFrame, fast: int, slow: int) -> bool:
    """MA_fast >= MA_slow"""
    close = df["Close"]
    needed = max(fast, slow)
    if len(close) < needed:
        return False
    ma_fast = sma(close, fast).iloc[-1]
    ma_slow = sma(close, slow).iloc[-1]
    return ma_fast >= ma_slow


def check_volume_range(df: pd.DataFrame, min_vol: int, max_vol: int) -> bool:
    """최근 봉 거래량 범위 확인"""
    if df.empty:
        return False
    vol = df["Volume"].iloc[-1]
    return min_vol <= vol <= max_vol


def check_market_cap(info: dict, min_usd: float) -> bool:
    """시가총액 최소값 확인"""
    cap = info.get("marketCap", 0) or 0
    return cap >= min_usd


def check_float_ratio(info: dict, min_pct: float) -> bool:
    """유통주식수 비율 확인"""
    shares_out = info.get("sharesOutstanding", 0) or 0
    float_shares = info.get("floatShares", 0) or 0
    if shares_out == 0:
        return False
    ratio = float_shares / shares_out * 100
    return ratio >= min_pct
