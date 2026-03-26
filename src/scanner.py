"""
전체 미장 스캔 엔진
API 호출 전략:
  Stage 1 - 일봉 배치 (100개씩): 거래량 선필터
  Stage 2 - 후보 종목을 타임프레임별로 배치 다운로드 후 평가
            (후보 500개 × 5타임프레임 = 개별 2500번 대신 타임프레임당 몇 번)
"""
import io
import os
import sys
import time
import logging
import threading
import contextlib
import yfinance as yf
import pandas as pd
from queue import Queue

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


@contextlib.contextmanager
def _suppress_yf_output():
    """yfinance의 'possibly delisted' 등 stdout/stderr 직접 출력 억제"""
    devnull = open(os.devnull, "w")
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = devnull, devnull
    try:
        yield
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        devnull.close()

from src.ticker_provider import get_us_tickers
from src.fetcher import _cache, _lock, CACHE_TTL, PERIOD_MAP
from src.evaluator import evaluate

DAILY_BATCH = 500
INTRA_BATCH = 200
MIN_CALL_INTERVAL = 0.2
ROUND_INTERVAL = 60


def _put(q: Queue, msg: dict):
    q.put(msg)


# ── Stage 1: 일봉 배치 거래량 필터 ───────────────────────────

def _batch_daily_volume_filter(
    tickers: list[str],
    min_vol: int,
    q: Queue,
    stop_event: threading.Event,
) -> list[str]:
    candidates = []
    total = len(tickers)

    for i in range(0, total, DAILY_BATCH):
        if stop_event.is_set():
            break
        batch = tickers[i : i + DAILY_BATCH]
        try:
            with _suppress_yf_output():
                df = yf.download(
                    " ".join(batch),
                    period="2d",
                    interval="1d",
                    progress=False,
                    auto_adjust=True,
                    prepost=True,
                    group_by="ticker",
                    threads=False,
                )
            for t in batch:
                vol = _extract_volume(df, t, len(batch))
                if vol is not None and vol >= min_vol:
                    candidates.append(t)
        except Exception as e:
            _put(q, {"type": "warn", "msg": f"배치 오류 ({batch[0]}~): {e}"})

        scanned = min(i + DAILY_BATCH, total)
        _put(q, {
            "type": "progress",
            "phase": 1,
            "scanned": scanned,
            "total": total,
            "candidates": len(candidates),
            "msg": f"[1단계] 거래량 필터 {scanned:,}/{total:,} | 후보 {len(candidates):,}개",
        })
        time.sleep(MIN_CALL_INTERVAL)

    return candidates


def _extract_volume(df, ticker, batch_size):
    """MultiIndex or flat DataFrame에서 최근 거래량 추출"""
    try:
        if df is None or df.empty:
            return None
        if not isinstance(df.columns, pd.MultiIndex):
            # 단일 종목 결과 (flat columns)
            return float(df["Volume"].dropna().iloc[-1]) if "Volume" in df.columns else None
        # group_by="ticker" → level 0: ticker, level 1: OHLCV
        tickers_in_df = df.columns.get_level_values(0).unique()
        if ticker in tickers_in_df:
            vol = df[ticker]["Volume"].dropna()
            return float(vol.iloc[-1]) if not vol.empty else None
        return None
    except Exception:
        return None


# ── Stage 2: 타임프레임별 배치 다운로드 + 캐시 주입 ──────────────

def _preload_timeframes(
    candidates: list[str],
    intervals: set[str],
    q: Queue,
    stop_event: threading.Event,
):
    """
    후보 종목을 타임프레임별로 배치 다운로드 → fetcher 캐시에 직접 주입.
    이렇게 하면 이후 evaluate()가 캐시 히트만 하게 됨.
    """
    for interval in intervals:
        if stop_event.is_set():
            break
        period = PERIOD_MAP.get(interval, "60d")
        ttl = CACHE_TTL.get(interval, 120)
        total = len(candidates)

        _put(q, {"type": "status", "msg": f"[2단계 준비] {interval} 데이터 로딩 중..."})

        for i in range(0, total, INTRA_BATCH):
            if stop_event.is_set():
                break
            batch = candidates[i : i + INTRA_BATCH]
            try:
                with _suppress_yf_output():
                    df_all = yf.download(
                        " ".join(batch),
                        period=period,
                        interval=interval,
                        progress=False,
                        auto_adjust=True,
                        prepost=True,
                        group_by="ticker",
                        threads=False,
                    )
                now = time.time()
                for t in batch:
                    df_t = _extract_ticker_df(df_all, t, len(batch))
                    if df_t is not None and not df_t.empty:
                        with _lock:
                            _cache[(t.upper(), interval)] = (now, df_t)
            except Exception as e:
                _put(q, {"type": "warn", "msg": f"{interval} 배치 오류: {e}"})

            loaded = min(i + INTRA_BATCH, total)
            _put(q, {
                "type": "progress",
                "phase": 2,
                "interval": interval,
                "loaded": loaded,
                "total": total,
                "msg": f"[2단계 준비] {interval} {loaded:,}/{total:,} 로드 완료",
            })
            time.sleep(MIN_CALL_INTERVAL)


def _extract_ticker_df(df_all, ticker, batch_size) -> pd.DataFrame | None:
    try:
        if df_all is None or df_all.empty:
            return None
        if not isinstance(df_all.columns, pd.MultiIndex):
            # 단일 종목 결과 (flat columns)
            return df_all.dropna()
        # group_by="ticker" → level 0: ticker, level 1: OHLCV
        tickers_in_df = df_all.columns.get_level_values(0).unique()
        if ticker in tickers_in_df:
            sub = df_all[ticker].dropna()
            return sub if not sub.empty else None
        return None
    except Exception:
        return None


# ── 공통: 라운드 대기 ──────────────────────────────────────────

def _wait_next_round(q: Queue, stop_event: threading.Event, round_num: int):
    """ROUND_INTERVAL초 대기하면서 매초 countdown 메시지 전송"""
    for remaining in range(ROUND_INTERVAL, 0, -1):
        if stop_event.is_set():
            return
        _put(q, {
            "type": "countdown",
            "round": round_num,
            "remaining": remaining,
            "msg": f"라운드 #{round_num} 완료 | 다음 스캔까지 {remaining}초",
        })
        time.sleep(1)


def _get_intervals_needed(logic: dict) -> set[str]:
    intervals = set()
    for c in logic["conditions"]:
        if c.get("enabled", True) and "interval" in c:
            intervals.add(c["interval"])
    intervals.discard("1d")
    return intervals


# ── 단일 라운드 실행 ───────────────────────────────────────────

def _run_universe_round(
    tickers: list[str],
    logic: dict,
    q: Queue,
    stop_event: threading.Event,
    round_num: int,
) -> list[dict]:
    """전체 미장 1라운드 수행 → 통과 종목 결과 리스트 반환"""
    vol_cond = next(
        (c for c in logic["conditions"] if c["type"] == "volume_range" and c.get("enabled", True)),
        None,
    )
    min_vol = int(vol_cond["min"]) if vol_cond else 300_000

    candidates = _batch_daily_volume_filter(tickers, min_vol, q, stop_event)
    if stop_event.is_set():
        return []

    _put(q, {"type": "status", "msg": f"후보 {len(candidates):,}개 → 기술적 분석 준비 중..."})

    intervals_needed = _get_intervals_needed(logic)
    if candidates and intervals_needed:
        _preload_timeframes(candidates, intervals_needed, q, stop_event)

    if stop_event.is_set():
        return []

    matches = []
    total_cands = len(candidates)
    _put(q, {"type": "status", "msg": f"[3단계] {total_cands:,}개 상세 조건 평가 중..."})

    for idx, ticker in enumerate(candidates):
        if stop_event.is_set():
            break
        try:
            result = evaluate(ticker, logic)
            if result["all_pass"]:
                matches.append(result)
        except Exception:
            pass

        if idx % 10 == 0 or idx == total_cands - 1:
            _put(q, {
                "type": "progress",
                "phase": 3,
                "scanned": idx + 1,
                "total": total_cands,
                "matches": len(matches),
                "msg": f"[3단계] {idx+1:,}/{total_cands:,} 평가 중 | {len(matches)}개 발견",
            })

    return matches


def _run_watchlist_round(
    tickers: list[str],
    logic: dict,
    q: Queue,
    stop_event: threading.Event,
    round_num: int,
) -> list[dict]:
    """즐겨찾기 1라운드 수행 → 통과 종목 결과 리스트 반환"""
    intervals_needed = _get_intervals_needed(logic)
    if intervals_needed:
        _preload_timeframes(tickers, intervals_needed, q, stop_event)

    if stop_event.is_set():
        return []

    matches = []
    total = len(tickers)
    for idx, ticker in enumerate(tickers):
        if stop_event.is_set():
            break
        try:
            result = evaluate(ticker, logic)
            if result["all_pass"]:
                matches.append(result)
        except Exception:
            pass
        _put(q, {
            "type": "progress",
            "phase": 3,
            "scanned": idx + 1,
            "total": total,
            "matches": len(matches),
            "msg": f"{ticker} 평가 완료 ({idx+1}/{total})",
        })

    return matches


# ── 메인 스캔 함수 (무한 루프 모니터링) ─────────────────────────

def scan_universe(logic: dict, q: Queue, stop_event: threading.Event):
    """전체 미장 실시간 모니터링. 라운드 단위로 무한 반복."""
    try:
        _put(q, {"type": "status", "msg": "종목 리스트 로딩 중..."})
        tickers = get_us_tickers()
        _put(q, {"type": "status", "msg": f"총 {len(tickers):,}개 종목 확인", "ticker_count": len(tickers)})

        round_num = 0
        while not stop_event.is_set():
            round_num += 1
            _put(q, {"type": "round_start", "round": round_num})

            matches = _run_universe_round(tickers, logic, q, stop_event, round_num)

            if stop_event.is_set():
                break

            _put(q, {
                "type": "round_done",
                "round": round_num,
                "matches": matches,
                "total_scanned": len(tickers),
                "timestamp": time.time(),
            })

            _wait_next_round(q, stop_event, round_num)

        _put(q, {"type": "done", "cancelled": True, "matches": 0})

    except Exception as e:
        _put(q, {"type": "error", "msg": str(e)})


def scan_watchlist(tickers: list[str], logic: dict, q: Queue, stop_event: threading.Event):
    """즐겨찾기 실시간 모니터링. 라운드 단위로 무한 반복."""
    try:
        if not tickers:
            _put(q, {"type": "error", "msg": "즐겨찾기 종목이 없습니다"})
            return

        round_num = 0
        while not stop_event.is_set():
            round_num += 1
            _put(q, {"type": "round_start", "round": round_num})

            matches = _run_watchlist_round(tickers, logic, q, stop_event, round_num)

            if stop_event.is_set():
                break

            _put(q, {
                "type": "round_done",
                "round": round_num,
                "matches": matches,
                "total_scanned": len(tickers),
                "timestamp": time.time(),
            })

            _wait_next_round(q, stop_event, round_num)

        _put(q, {"type": "done", "cancelled": True, "matches": 0})

    except Exception as e:
        _put(q, {"type": "error", "msg": str(e)})
