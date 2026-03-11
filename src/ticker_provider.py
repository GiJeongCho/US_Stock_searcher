"""
미국 전체 상장 종목 리스트 제공
출처: NASDAQ Trader FTP (무료, 매일 갱신)
"""
import os
import json
import time
import re
import urllib.request

NASDAQ_URL = "https://ftp.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
CACHE_FILE = os.path.join(os.path.dirname(__file__), "../config/us_tickers.json")
CACHE_TTL = 86400  # 24시간


def get_us_tickers() -> list[str]:
    """
    미국 전체 종목 리스트 반환 (캐시 우선, 24h 갱신)
    ETF, 테스트 이슈, 특수기호 종목 제외
    """
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) < CACHE_TTL:
            return data["tickers"]

    print("[ticker_provider] NASDAQ에서 종목 리스트 다운로드 중...")
    tickers = _fetch_nasdaq_tickers()
    print(f"[ticker_provider] {len(tickers)}개 종목 로드 완료")

    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump({"ts": time.time(), "tickers": tickers}, f)

    return tickers


def _fetch_nasdaq_tickers() -> list[str]:
    try:
        req = urllib.request.Request(NASDAQ_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8")
    except Exception as e:
        print(f"[ticker_provider] NASDAQ 다운로드 실패: {e}, 캐시 확인 중...")
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE) as f:
                return json.load(f).get("tickers", [])
        raise

    lines = content.strip().split("\n")
    tickers = []

    for line in lines[1:]:  # 첫 줄은 헤더
        if line.startswith("File"):  # 마지막 줄 (파일 생성 정보)
            break
        parts = line.split("|")
        if len(parts) < 9:
            continue

        symbol = parts[0].strip()
        etf = parts[4].strip()      # Y = ETF
        test = parts[7].strip()     # Y = 테스트 종목

        # ETF, 테스트 종목 제외
        if etf == "Y" or test == "Y":
            continue

        # 알파벳 1~5자리 단순 심볼만 (워런트, 우선주 등 제외)
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
