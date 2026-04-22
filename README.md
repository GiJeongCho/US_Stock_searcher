# US Stock Screener

미국 전체 상장 종목을 대상으로 기술적 조건을 평가하는 웹 기반 검색기.

---

## 구현 현황

- [x] 전체 미장 종목 유니버스 (NASDAQ FTP, ~7,000개)
- [x] 거래량 선필터 → 기술적 조건 평가 2단계 스캔
- [x] 조건별 수치 웹 UI에서 직접 수정
- [x] SSE 실시간 진행 스트리밍
- [x] gunicorn + systemd 상시 구동
- [ ] 알림 발송 (Telegram 등)
- [ ] 코인 검색기

---

## 실행

```bash
# 개발 서버
python app.py

# 운영 (서비스 등록 후)
sudo systemctl start stock-screener
sudo systemctl status stock-screener
```

접속: `http://<서버IP>:4999`

---

## 디렉토리 구조

```
US_Stock_searcher/
├── app.py                      # Flask 앱 · API 라우트 · SSE 스트리밍
├── start.sh                    # gunicorn 실행 스크립트
├── requirements.txt
├── config/
│   ├── logic1.json             # 급등이 조건 설정
│   ├── logic2.json             # 벌떡이 조건 설정
│   ├── watchlist.json          # 즐겨찾기 종목 (자동 생성)
│   └── us_tickers.json         # 종목 유니버스 캐시 (24h, 자동 생성)
├── src/
│   ├── ticker_provider.py      # NASDAQ FTP에서 전체 종목 리스트 수집
│   ├── fetcher.py              # yfinance 래퍼 · 인터벌별 캐시
│   ├── indicators.py           # SMA · 볼린저밴드 · 엔벨로프 계산
│   ├── evaluator.py            # 종목 × 로직 조건 평가
│   └── scanner.py              # 전체 스캔 엔진 (배치 + 캐시 주입)
└── templates/
    └── index.html              # 웹 UI (SSE 수신 · 조건 편집)
```

---

## 검색기 로직

### 급등이 (`config/logic1.json`)

| # | 조건 | 파라미터 |
|---|------|----------|
| 1 | 5분 이평배열 | MA5 ≥ MA20 ≥ MA60 |
| 2 | 5분 이평배열 | MA5 ≥ MA20 ≥ MA200 |
| 3 | 5분 이평배열 | MA5 ≥ MA100 ≥ MA150 |
| 4 | 5분 볼린저밴드 상향돌파 | 기간 20, 표준편차 2.0 |
| 5 | 5분 볼린저밴드 상한선 이상 | 기간 20, 표준편차 2.0 |
| 6 | 일 거래량 | 300,000 ~ 999,999,999 |
| 7 | 5분 이평이격도 MA5-MA200 | 10% 이내 |
| 8 | 5분 이평이격도 MA5-MA20 | 5% 이내 |
| 9 | 시가총액 | $30M 이상 |
| 10 | 5분 MA5 ≥ MA224 | — |
| 11 | 5분 엔벨로프 상향돌파 | 기간 12, 2.2% |
| 12 | 5분 엔벨로프 상향돌파 | 기간 20, 3.3% |
| 13 | 유통비율 | 20% 이상 (비활성화) |
| 14 | 60분 이평배열 | MA5 ≥ MA20 ≥ MA60 |
| 15 | 60분 이평배열 | MA20 ≥ MA60 ≥ MA112 |
| 16 | 60분 이평배열 | MA20 ≥ MA112 ≥ MA250 |

### 벌떡이 (`config/logic2.json`)

| # | 조건 | 파라미터 |
|---|------|----------|
| 1 | 일 거래량 | 300,000 ~ 999,999,999 |
| 2 | 시가총액 | $22M 이상 |
| 3 | 1분 이평배열 | MA5 ≥ MA60 ≥ MA112 |
| 4 | 1분 이평배열 | MA5 ≥ MA112 ≥ MA224 |
| 5 | 5분 이평배열 | MA5 ≥ MA20 ≥ MA60 |
| 6 | 5분 이평배열 | MA60 ≥ MA100 ≥ MA200 |
| 7 | 5분 이평이격도 MA5-MA224 | 10% 이내 |
| 8 | 15분 이평배열 | MA5 ≥ MA20 ≥ MA60 |
| 9 | 15분 이평배열 | MA20 ≥ MA60 ≥ MA200 |
| 10 | 일봉 MA5 ≥ MA10 | — |
| 11 | 5분 엔벨로프 상향돌파 | 기간 12, 3.0% |
| 12 | 1분 엔벨로프 상향돌파 | 기간 12, 2.0% |

> 모든 수치는 웹 UI 왼쪽 패널에서 실시간 수정 후 저장 가능.

---

## API 호출 전략 (rate limit 대응)

전체 ~7,000종목을 효율적으로 스캔하기 위해 3단계로 나눔.

```
Stage 1 │ yf.download() 100종목씩 배치  →  일봉 거래량 선필터
        │ ~70번 API 호출 / 약 3분
        ↓
Stage 2 │ yf.download() 50종목씩 배치  →  타임프레임별 데이터 프리로드
        │ 후보(~수백개) × 타임프레임 수 / 50번 호출 / 약 2분
        ↓
Stage 3 │ 캐시에서만 읽음              →  조건 평가 (API 호출 0)
        │ 약 1분
```

| 항목 | 값 |
|------|-----|
| 일봉 배치 크기 | 100종목 |
| 분봉 배치 크기 | 50종목 |
| 배치 간 대기 | 0.3초 |
| 개별 캐시 TTL | 1m=30s / 5m=2m / 15m=5m / 60m=10m / 1d=10m |

---

## 지원 조건 타입

| type | 설명 |
|------|------|
| `ma_alignment` | MA_a ≥ MA_b ≥ MA_c 정배열 |
| `ma_compare` | MA_fast ≥ MA_slow |
| `ma_gap` | \|MA_fast − MA_slow\| / MA_slow ≤ N% |
| `bb_breakout` | 볼린저밴드 상한선 상향돌파 (전봉 이하 → 현봉 이상) |
| `bb_above` | 볼린저밴드 상한선 이상 |
| `envelope_breakout` | 엔벨로프 상한선 상향돌파 |
| `volume_range` | 거래량 min ~ max |
| `market_cap_min` | 시가총액 ≥ N USD |
| `float_ratio_min` | 유통비율 ≥ N% |

---

## 서비스 관리

```bash
sudo systemctl start   stock-screener   # 시작
sudo systemctl stop    stock-screener   # 중지
sudo systemctl restart stock-screener   # 재시작
sudo systemctl enable  stock-screener   # 부팅 시 자동 시작 등록
journalctl -u stock-screener -f         # 실시간 로그
```

---

## 의존성 설치

```bash
pip install flask yfinance pandas numpy gunicorn
```
