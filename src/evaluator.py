"""
종목 + 로직 조건 평가기
"""
from src.fetcher import get_ohlcv, get_info
from src import indicators as ind


def _eval_condition(cond: dict, ticker: str, df_cache: dict, info: dict) -> dict:
    """단일 조건 평가 → {id, label, pass, reason}"""
    cid = cond["id"]
    label = cond["label"]
    enabled = cond.get("enabled", True)

    if not enabled:
        return {"id": cid, "label": label, "pass": None, "reason": "비활성화"}

    ctype = cond["type"]
    result = False
    reason = ""

    try:
        interval = cond.get("interval")

        # DataFrame 가져오기 (캐시)
        def get_df(iv):
            if iv not in df_cache:
                df_cache[iv] = get_ohlcv(ticker, iv)
            return df_cache[iv]

        if ctype == "ma_alignment":
            df = get_df(interval)
            if df is None:
                reason = "데이터 없음"
            else:
                result = ind.check_ma_alignment(df, cond["periods"])

        elif ctype == "bb_breakout":
            df = get_df(interval)
            if df is None:
                reason = "데이터 없음"
            else:
                result = ind.check_bb_breakout(df, cond["period"], cond["std"])

        elif ctype == "bb_above":
            df = get_df(interval)
            if df is None:
                reason = "데이터 없음"
            else:
                result = ind.check_bb_above(df, cond["period"], cond["std"])

        elif ctype == "envelope_breakout":
            df = get_df(interval)
            if df is None:
                reason = "데이터 없음"
            else:
                result = ind.check_envelope_breakout(df, cond["period"], cond["pct"])

        elif ctype == "ma_gap":
            df = get_df(interval)
            if df is None:
                reason = "데이터 없음"
            else:
                result = ind.check_ma_gap(df, cond["fast"], cond["slow"], cond["threshold_pct"])

        elif ctype == "ma_compare":
            df = get_df(interval)
            if df is None:
                reason = "데이터 없음"
            else:
                result = ind.check_ma_compare(df, cond["fast"], cond["slow"])

        elif ctype == "volume_range":
            df = get_df(interval)
            if df is None:
                reason = "데이터 없음"
            else:
                result = ind.check_volume_range(df, cond["min"], cond["max"])

        elif ctype == "market_cap_min":
            result = ind.check_market_cap(info, cond["min_usd"])

        elif ctype == "float_ratio_min":
            result = ind.check_float_ratio(info, cond["min_pct"])

        else:
            reason = f"알 수 없는 조건 타입: {ctype}"

    except Exception as e:
        reason = f"오류: {e}"

    return {"id": cid, "label": label, "pass": result, "reason": reason}


def evaluate(ticker: str, logic: dict) -> dict:
    """
    ticker 에 대해 logic 전체 조건 평가
    반환: {ticker, logic_name, conditions: [...], all_pass: bool}
    """
    df_cache: dict = {}
    info = get_info(ticker)

    results = []
    for cond in logic.get("conditions", []):
        r = _eval_condition(cond, ticker, df_cache, info)
        results.append(r)

    enabled_results = [r for r in results if r["pass"] is not None]
    all_pass = all(r["pass"] for r in enabled_results) if enabled_results else False

    return {
        "ticker": ticker,
        "logic_name": logic.get("name", ""),
        "conditions": results,
        "all_pass": all_pass,
        "pass_count": sum(1 for r in enabled_results if r["pass"]),
        "total_count": len(enabled_results),
    }
