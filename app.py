"""
US Stock Screener - Flask 웹 앱
백그라운드에서 자동 모니터링, 프론트는 결과 뷰어
"""
import json
import os
import threading
import time
from datetime import datetime
import numpy as np
import bcrypt
from flask import Flask, jsonify, render_template, request, redirect, url_for
from flask.json.provider import DefaultJSONProvider
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker


class NumpyJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, (np.bool_, np.integer)):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "us-stock-screener-secret-key-change-me")
app.json_provider_class = NumpyJSONProvider
app.json = NumpyJSONProvider(app)

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "users.db")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# ── DB 설정 ──────────────────────────────────────────────────
Base = declarative_base()


class User(Base, UserMixin):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    watchlist_json = Column(Text, default="[]")

    def set_password(self, pw: str):
        self.password_hash = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

    def check_password(self, pw: str) -> bool:
        return bcrypt.checkpw(pw.encode(), self.password_hash.encode())

    def get_watchlist(self) -> list:
        return json.loads(self.watchlist_json or "[]")

    def set_watchlist(self, tickers: list):
        self.watchlist_json = json.dumps(tickers)


engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_page"


@login_manager.user_loader
def load_user(user_id):
    s = Session()
    try:
        return s.get(User, int(user_id))
    finally:
        s.close()


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "login_required"}), 401
    return redirect(url_for("login_page"))

# ── 공유 스캔 상태 (백그라운드 스캐너 ↔ API) ──────────────────
_state_lock = threading.Lock()
_scan_state = {
    "running": False,
    "round": 0,
    "phase": 0,
    "status_msg": "서버 시작 중...",
    "progress_pct": 0,
    "logic_id": "logic2",
    # ticker -> {result, round, found_at(ISO), updated_at(ISO)}
    "active": {},
    # ticker -> {result, round, found_at(ISO), exited_at(ISO)}
    "history": {},
    # 조건별 통과/탈락 통계 (라운드 완료 시 갱신)
    "cond_stats": {},
}


def _update_state(**kwargs):
    with _state_lock:
        _scan_state.update(kwargs)


def _get_state() -> dict:
    with _state_lock:
        return dict(_scan_state)


# ── 설정 파일 헬퍼 ──────────────────────────────────────────

def load_logic(logic_id: str) -> dict:
    path = os.path.join(CONFIG_DIR, f"{logic_id}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_logic(logic_id: str, data: dict):
    path = os.path.join(CONFIG_DIR, f"{logic_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 인증 ──────────────────────────────────────────────────────

@app.route("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "아이디와 비밀번호를 입력해주세요"}), 400
    if len(username) < 2 or len(username) > 20:
        return jsonify({"error": "아이디는 2~20자"}), 400
    if len(password) < 4:
        return jsonify({"error": "비밀번호는 4자 이상"}), 400

    s = Session()
    try:
        if s.query(User).filter_by(username=username).first():
            return jsonify({"error": "이미 존재하는 아이디입니다"}), 409
        user = User(username=username)
        user.set_password(password)
        s.add(user)
        s.commit()
        login_user(user, remember=True)
        return jsonify({"ok": True, "username": username})
    finally:
        s.close()


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    s = Session()
    try:
        user = s.query(User).filter_by(username=username).first()
        if not user or not user.check_password(password):
            return jsonify({"error": "아이디 또는 비밀번호가 틀렸습니다"}), 401
        login_user(user, remember=True)
        return jsonify({"ok": True, "username": username})
    finally:
        s.close()


@app.route("/api/logout", methods=["POST"])
def logout():
    logout_user()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    if current_user.is_authenticated:
        return jsonify({"logged_in": True, "username": current_user.username})
    return jsonify({"logged_in": False})


# ── 페이지 ──────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html")


# ── 로직 설정 ────────────────────────────────────────────────

@app.route("/api/logic/<logic_id>", methods=["GET"])
def get_logic(logic_id):
    if logic_id not in ("logic1", "logic2"):
        return jsonify({"error": "not found"}), 404
    return jsonify(load_logic(logic_id))


@app.route("/api/logic/<logic_id>", methods=["POST"])
def update_logic(logic_id):
    if logic_id not in ("logic1", "logic2"):
        return jsonify({"error": "not found"}), 404
    save_logic(logic_id, request.get_json())
    return jsonify({"ok": True})


# ── 와치리스트 ───────────────────────────────────────────────

@app.route("/api/watchlist", methods=["GET"])
@login_required
def get_watchlist():
    s = Session()
    try:
        user = s.get(User, current_user.id)
        return jsonify(user.get_watchlist())
    finally:
        s.close()


@app.route("/api/watchlist", methods=["POST"])
@login_required
def add_watchlist():
    ticker = (request.get_json() or {}).get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "ticker 필요"}), 400
    s = Session()
    try:
        user = s.get(User, current_user.id)
        wl = user.get_watchlist()
        if ticker not in wl:
            wl.append(ticker)
            user.set_watchlist(wl)
            s.commit()
        return jsonify({"ok": True, "watchlist": wl})
    finally:
        s.close()


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
@login_required
def del_watchlist(ticker):
    s = Session()
    try:
        user = s.get(User, current_user.id)
        wl = [t for t in user.get_watchlist() if t != ticker.upper()]
        user.set_watchlist(wl)
        s.commit()
        return jsonify({"ok": True, "watchlist": wl})
    finally:
        s.close()


# ── 종목 유니버스 정보 ────────────────────────────────────────

@app.route("/api/universe/info", methods=["GET"])
def universe_info():
    from src.ticker_provider import get_ticker_count
    return jsonify(get_ticker_count())


@app.route("/api/universe/refresh", methods=["POST"])
def universe_refresh():
    from src.ticker_provider import force_refresh
    tickers = force_refresh()
    return jsonify({"ok": True, "count": len(tickers)})


# ── 스캔 결과 조회 (프론트 폴링용) ─────────────────────────────

@app.route("/api/results", methods=["GET"])
def get_results():
    st = _get_state()
    return jsonify({
        "running": st["running"],
        "round": st["round"],
        "phase": st["phase"],
        "status_msg": st["status_msg"],
        "progress_pct": st["progress_pct"],
        "logic_id": st["logic_id"],
        "active": list(st["active"].values()),
        "history": list(st["history"].values()),
        "cond_stats": st.get("cond_stats", {}),
    })


@app.route("/api/switch_logic", methods=["POST"])
def switch_logic():
    logic_id = (request.get_json() or {}).get("logic_id", "")
    if logic_id not in ("logic1", "logic2"):
        return jsonify({"error": "invalid logic_id"}), 400
    with _state_lock:
        if _scan_state["logic_id"] != logic_id:
            _scan_state["logic_id"] = logic_id
            _scan_state["active"] = {}
            _scan_state["history"] = {}
            _scan_state["cond_stats"] = {}
    return jsonify({"ok": True, "logic_id": logic_id})


# ── 백그라운드 스캐너 ────────────────────────────────────────

_bg_stop = threading.Event()


def _drain_queue_to_state(q, round_num):
    """Queue에 쌓인 scanner 메시지를 소비해서 _scan_state에 반영"""
    from queue import Empty
    while True:
        try:
            msg = q.get_nowait()
        except Empty:
            break
        mtype = msg.get("type")
        if mtype == "progress":
            phase = msg.get("phase", 1)
            total = msg.get("total", 1) or 1
            scanned = msg.get("scanned", msg.get("loaded", 0))
            pct_in_phase = scanned / total * 100
            if phase == 1:
                pct = pct_in_phase * 0.3
            elif phase == 2:
                pct = 30 + pct_in_phase * 0.3
            else:
                pct = 60 + pct_in_phase * 0.4
            _update_state(phase=phase, progress_pct=int(pct),
                          status_msg=msg.get("msg", ""))
        elif mtype == "status":
            _update_state(status_msg=msg.get("msg", ""))


def _background_scanner():
    """서버 시작과 동시에 선택된 로직으로 무한 모니터링"""
    from queue import Queue
    from src.scanner import (
        ROUND_INTERVAL, _batch_daily_volume_filter,
        _preload_timeframes, _get_intervals_needed,
    )
    from src.ticker_provider import get_us_tickers
    from src.evaluator import evaluate

    _update_state(running=True, status_msg="종목 리스트 로딩 중...")

    tickers = None
    for attempt in range(5):
        if _bg_stop.is_set():
            return
        try:
            tickers = get_us_tickers()
            break
        except Exception as e:
            wait = 10 * (attempt + 1)
            _update_state(status_msg=f"종목 로드 실패 (재시도 {attempt+1}/5): {e} | {wait}초 후 재시도")
            time.sleep(wait)

    if not tickers:
        _update_state(running=False, status_msg="종목 로드 최종 실패 — 서버 재시작 필요")
        return

    _update_state(status_msg=f"총 {len(tickers):,}개 종목 확인")
    q = Queue()

    round_num = 0
    while not _bg_stop.is_set():
        round_num += 1
        logic_id = _scan_state["logic_id"]
        try:
            logic = load_logic(logic_id)
        except Exception:
            logic = load_logic("logic1")

        _update_state(round=round_num, phase=1, progress_pct=0,
                      status_msg=f"R#{round_num} 거래량 필터 중...")

        vol_cond = next(
            (c for c in logic["conditions"]
             if c["type"] == "volume_range" and c.get("enabled", True)),
            None,
        )
        min_vol = int(vol_cond["min"]) if vol_cond else 300_000

        import concurrent.futures
        def _vol_filter_with_progress():
            result = _batch_daily_volume_filter(tickers, min_vol, q, _bg_stop)
            return result

        vol_future = concurrent.futures.ThreadPoolExecutor(1).submit(_vol_filter_with_progress)
        while not vol_future.done():
            _drain_queue_to_state(q, round_num)
            time.sleep(0.5)
        candidates = vol_future.result()
        _drain_queue_to_state(q, round_num)
        if _bg_stop.is_set():
            break

        _update_state(phase=2, progress_pct=30,
                      status_msg=f"R#{round_num} 후보 {len(candidates):,}개 데이터 로드 중...")

        intervals_needed = _get_intervals_needed(logic)
        if candidates and intervals_needed:
            preload_future = concurrent.futures.ThreadPoolExecutor(1).submit(
                _preload_timeframes, candidates, intervals_needed, q, _bg_stop
            )
            while not preload_future.done():
                _drain_queue_to_state(q, round_num)
                time.sleep(0.5)
            preload_future.result()
            _drain_queue_to_state(q, round_num)
        if _bg_stop.is_set():
            break

        _update_state(phase=3, progress_pct=60,
                      status_msg=f"R#{round_num} {len(candidates):,}개 조건 평가 중...")

        matches = []
        cond_stats: dict[str, dict] = {}
        for idx, ticker in enumerate(candidates):
            if _bg_stop.is_set():
                break
            try:
                result = evaluate(ticker, logic)
                for c in result["conditions"]:
                    cid = c["id"]
                    if cid not in cond_stats:
                        cond_stats[cid] = {"label": c["label"], "pass": 0, "fail": 0, "skip": 0}
                    if c["pass"] is True:
                        cond_stats[cid]["pass"] += 1
                    elif c["pass"] is False:
                        cond_stats[cid]["fail"] += 1
                    else:
                        cond_stats[cid]["skip"] += 1
                if result["all_pass"]:
                    matches.append(result)
            except Exception:
                pass

            if idx % 20 == 0:
                pct = 60 + (idx / max(len(candidates), 1)) * 40
                _update_state(progress_pct=int(pct),
                              status_msg=f"R#{round_num} {idx+1:,}/{len(candidates):,} 평가 | {len(matches)}개 발견")

        _drain_queue_to_state(q, round_num)
        if _bg_stop.is_set():
            break

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_tickers = {r["ticker"] for r in matches}

        with _state_lock:
            old_active = dict(_scan_state["active"])

            for ticker_key, data in old_active.items():
                if ticker_key not in new_tickers:
                    _scan_state["history"][ticker_key] = {
                        "result": data["result"],
                        "round": data["round"],
                        "found_at": data["found_at"],
                        "exited_at": now_iso,
                    }
                    del _scan_state["active"][ticker_key]

            for r in matches:
                tk = r["ticker"]
                existing = _scan_state["active"].get(tk)
                _scan_state["active"][tk] = {
                    "result": r,
                    "round": round_num,
                    "found_at": existing["found_at"] if existing else now_iso,
                    "updated_at": now_iso,
                }
                _scan_state["history"].pop(tk, None)

            vol_stats = {
                "label": "거래량 필터 (1단계)",
                "pass": len(candidates),
                "fail": len(tickers) - len(candidates),
                "skip": 0,
            }
            _scan_state["cond_stats"] = {"_volume_filter": vol_stats, **cond_stats}
            _scan_state["phase"] = -1
            _scan_state["progress_pct"] = 100
            _scan_state["status_msg"] = (
                f"R#{round_num} 완료 | 충족 {len(_scan_state['active'])}개 "
                f"| 이력 {len(_scan_state['history'])}개 | {now_iso}"
            )

        for remaining in range(ROUND_INTERVAL, 0, -1):
            if _bg_stop.is_set():
                break
            _update_state(
                status_msg=f"R#{round_num} 완료 | 다음 스캔까지 {remaining}초",
                progress_pct=int(100 - (remaining / ROUND_INTERVAL * 100)),
            )
            time.sleep(1)

    _update_state(running=False, status_msg="모니터링 중지됨")


_bg_thread = None


def start_background_scanner():
    global _bg_thread
    if _bg_thread and _bg_thread.is_alive():
        return
    _bg_stop.clear()
    _bg_thread = threading.Thread(target=_background_scanner, daemon=True)
    _bg_thread.start()


_scanner_started = False

@app.before_request
def _ensure_scanner():
    global _scanner_started
    if not _scanner_started:
        _scanner_started = True
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
            start_background_scanner()


if __name__ == "__main__":
    import sys
    if "--reload" in sys.argv:
        app.run(debug=True, use_reloader=True, host="0.0.0.0", port=5065, threaded=True)
    else:
        from waitress import serve
        start_background_scanner()
        print("🚀 Waitress 서버 시작: http://0.0.0.0:5065")
        serve(app, host="0.0.0.0", port=5065, threads=8)
