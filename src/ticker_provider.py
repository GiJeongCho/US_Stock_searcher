"""
미국 전체 상장 종목 리스트 제공
1차: GitHub US-Stock-Symbols (빠르고 안정적)
2차: NASDAQ Trader FTP (fallback)
"""
import os
import json
import time
import re
import urllib.request

GITHUB_URL = "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/all/all_tickers.txt"
NASDAQ_URL = "https://ftp.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
CACHE_FILE = os.path.join(os.path.dirname(__file__), "../config/us_tickers.json")
CACHE_TTL = 86400  # 24시간
_UA = {"User-Agent": "Mozilla/5.0"}


def get_us_tickers() -> list[str]:
    """
    미국 전체 종목 리스트 반환 (캐시 우선, 24h 갱신)
    """
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) < CACHE_TTL:
            return data["tickers"]

    tickers = _fetch_with_fallback()

    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump({"ts": time.time(), "tickers": tickers}, f)

    return tickers


def _fetch_with_fallback() -> list[str]:
    """GitHub → NASDAQ FTP → 만료 캐시 순으로 시도"""
    for name, fn in [("GitHub", _fetch_github), ("NASDAQ FTP", _fetch_nasdaq)]:
        try:
            print(f"[ticker_provider] {name}에서 종목 리스트 다운로드 중...")
            tickers = fn()
            if tickers:
                print(f"[ticker_provider] {name}: {len(tickers)}개 종목 로드 완료")
                return tickers
        except Exception as e:
            print(f"[ticker_provider] {name} 실패: {e}")

    if os.path.exists(CACHE_FILE):
        print("[ticker_provider] 모든 소스 실패, 만료 캐시 사용")
        with open(CACHE_FILE) as f:
            return json.load(f).get("tickers", [])

    raise RuntimeError("종목 리스트를 가져올 수 없습니다")


def _fetch_github() -> list[str]:
    req = urllib.request.Request(GITHUB_URL, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        lines = resp.read().decode("utf-8").strip().split("\n")

    tickers = []
    for sym in lines:
        sym = sym.strip().upper()
        if re.match(r"^[A-Z]{1,5}$", sym):
            tickers.append(sym)
    return tickers


def _fetch_nasdaq() -> list[str]:
    req = urllib.request.Request(NASDAQ_URL, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        content = resp.read().decode("utf-8")

    lines = content.strip().split("\n")
    tickers = []

    for line in lines[1:]:
        if line.startswith("File"):
            break
        parts = line.split("|")
        if len(parts) < 9:
            continue

        symbol = parts[0].strip()
        etf = parts[4].strip()
        test = parts[7].strip()

        if etf == "Y" or test == "Y":
            continue
        if not re.match(r"^[A-Z]{1,5}$", symbol):
            continue

        tickers.append(symbol)

    return tickers


def force_refresh() -> list[str]:
    """캐시 무시하고 강제 갱신"""
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    return get_us_tickers()


def get_ticker_count() -> dict:
    """캐시 상태 반환"""
    if not os.path.exists(CACHE_FILE):
        return {"cached": False, "count": 0}
    with open(CACHE_FILE) as f:
        data = json.load(f)
    age_hours = (time.time() - data.get("ts", 0)) / 3600
    return {
        "cached": True,
        "count": len(data.get("tickers", [])),
        "age_hours": round(age_hours, 1),
        "stale": age_hours > 24,
    }
