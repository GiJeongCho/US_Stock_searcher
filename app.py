"""
US Stock Screener - Flask 웹 앱
"""
import json
import os
import threading
import time
from queue import Queue, Empty
from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")
WATCHLIST_FILE = os.path.join(CONFIG_DIR, "watchlist.json")

# 스캔 세션 관리
_scans: dict[str, dict] = {}   # scan_id -> {queue, stop_event, thread}
_scans_lock = threading.Lock()


# ── 설정 파일 헬퍼 ──────────────────────────────────────────

def load_logic(logic_id: str) -> dict:
    path = os.path.join(CONFIG_DIR, f"{logic_id}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_logic(logic_id: str, data: dict):
    path = os.path.join(CONFIG_DIR, f"{logic_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_watchlist() -> list:
    if not os.path.exists(WATCHLIST_FILE):
        return []
    with open(WATCHLIST_FILE) as f:
        return json.load(f)


def save_watchlist(tickers: list):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(tickers, f)


# ── 페이지 ──────────────────────────────────────────────────

@app.route("/")
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
def get_watchlist():
    return jsonify(load_watchlist())


@app.route("/api/watchlist", methods=["POST"])
def add_watchlist():
    ticker = (request.get_json() or {}).get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "ticker 필요"}), 400
    wl = load_watchlist()
    if ticker not in wl:
        wl.append(ticker)
        save_watchlist(wl)
    return jsonify({"ok": True, "watchlist": wl})


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
def del_watchlist(ticker):
    wl = [t for t in load_watchlist() if t != ticker.upper()]
    save_watchlist(wl)
    return jsonify({"ok": True, "watchlist": wl})


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


# ── 스캔 시작 (SSE 스트리밍) ─────────────────────────────────

@app.route("/api/scan/start", methods=["POST"])
def scan_start():
    body = request.get_json() or {}
    logic_id = body.get("logic_id", "logic1")
    mode = body.get("mode", "universe")   # "universe" | "watchlist"
    logic = load_logic(logic_id)

    scan_id = str(int(time.time() * 1000))
    q = Queue()
    stop_event = threading.Event()

    def run():
        from src.scanner import scan_universe, scan_watchlist
        if mode == "watchlist":
            tickers = load_watchlist()
            if not tickers:
                q.put({"type": "error", "msg": "즐겨찾기 종목이 없습니다"})
                return
            scan_watchlist(tickers, logic, q, stop_event)
        else:
            scan_universe(logic, q, stop_event)

    t = threading.Thread(target=run, daemon=True)
    with _scans_lock:
        _scans[scan_id] = {"queue": q, "stop": stop_event, "thread": t}
    t.start()

    return jsonify({"scan_id": scan_id})


@app.route("/api/scan/stream/<scan_id>")
def scan_stream(scan_id):
    with _scans_lock:
        sess = _scans.get(scan_id)
    if not sess:
        return jsonify({"error": "세션 없음"}), 404

    q: Queue = sess["queue"]

    def generate():
        while True:
            try:
                msg = q.get(timeout=20)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg.get("type") in ("done", "error"):
                    # 세션 정리
                    with _scans_lock:
                        _scans.pop(scan_id, None)
                    break
            except Empty:
                yield "data: {\"type\":\"heartbeat\"}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/scan/stop/<scan_id>", methods=["POST"])
def scan_stop(scan_id):
    with _scans_lock:
        sess = _scans.get(scan_id)
    if sess:
        sess["stop"].set()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001, threaded=True)
