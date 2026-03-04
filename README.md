# US Stock Alarm

미국 주식 시장 모니터링 및 알람 시스템

---

## 개요

지정한 종목의 가격 조건이 충족될 때 알림을 보내는 CLI 기반 알람기.
실시간 시세 조회 → 조건 평가 → 알림 발송의 단순한 루프 구조.

---

## 기능 계획

### 핵심 기능
- **가격 알람**: 지정 가격 이상/이하 도달 시 알림
- **등락률 알람**: 전일 대비 ±N% 변동 시 알림
- **다중 종목 감시**: 여러 티커를 동시에 모니터링
- **알람 조건 파일 관리**: JSON/YAML로 알람 규칙 저장·불러오기

### 부가 기능 (선택)
- 장 시작/마감 시간 자동 인식 (09:30~16:00 ET)
- 알람 발송 이력 로깅
- Telegram / Discord / 데스크탑 알림 채널 선택

---

## 기술 스택 후보

| 구분 | 옵션 A | 옵션 B |
|------|--------|--------|
| 언어 | Python | Go |
| 시세 API | `yfinance` (무료) | Alpha Vantage / Polygon.io |
| 알림 채널 | Telegram Bot API | 데스크탑 notify-send |
| 스케줄 | `schedule` 라이브러리 | cron |

> 기본 방향: **Python + yfinance + Telegram**

---

## 예상 디렉토리 구조

```
US_Stock_searcher/
├── README.md
├── requirements.txt
├── config/
│   ├── alarms.json          # 알람 규칙 목록
│   └── settings.json        # API 키, 알림 채널 설정
├── src/
│   ├── main.py              # 진입점 · 메인 루프
│   ├── fetcher.py           # 시세 조회 (yfinance 래핑)
│   ├── evaluator.py         # 알람 조건 평가 로직
│   ├── notifier.py          # 알림 발송 (Telegram 등)
│   └── alarm_manager.py     # 알람 규칙 CRUD
└── logs/
    └── alarm_history.log
```

---

## 알람 규칙 형식 (alarms.json 예시)

```json
[
  {
    "id": "aapl-break-200",
    "ticker": "AAPL",
    "condition": "price_above",
    "target": 200.0,
    "message": "AAPL $200 돌파!",
    "active": true,
    "once": true
  },
  {
    "id": "nvda-drop-5pct",
    "ticker": "NVDA",
    "condition": "change_below",
    "target": -5.0,
    "message": "NVDA 5% 이상 하락",
    "active": true,
    "once": false
  }
]
```

### 지원 조건 타입

| condition | 설명 |
|-----------|------|
| `price_above` | 현재가 ≥ target |
| `price_below` | 현재가 ≤ target |
| `change_above` | 등락률 ≥ target% |
| `change_below` | 등락률 ≤ target% |

---

## 실행 흐름

```
main.py 시작
  └─ alarms.json 로드
  └─ 루프 (N초 간격)
       ├─ fetcher: 감시 종목 시세 일괄 조회
       ├─ evaluator: 각 알람 조건 평가
       │    └─ 조건 충족 → notifier 호출
       └─ 대기 (기본 60초, 장 외 시간은 슬립)
```

---

## 설정 (settings.json 예시)

```json
{
  "interval_seconds": 60,
  "telegram": {
    "bot_token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID"
  },
  "market_hours": {
    "open": "09:30",
    "close": "16:00",
    "timezone": "America/New_York"
  }
}
```

---

## 구현 순서 (로드맵)

1. `fetcher.py` — yfinance로 단일 종목 시세 조회
2. `evaluator.py` — 조건 평가 함수
3. `notifier.py` — Telegram 메시지 발송
4. `alarm_manager.py` — alarms.json CRUD
5. `main.py` — 루프 조립 + 장 시간 체크
6. CLI 인터페이스 추가 (알람 추가/삭제/목록)
7. (선택) 비트코인 모드 분리 또는 통합

---

## 사용 예시 (목표 CLI)

```bash
# 알람 추가
python main.py add --ticker AAPL --condition price_above --target 200

# 알람 목록 확인
python main.py list

# 모니터링 시작
python main.py run

# 특정 알람 비활성화
python main.py disable aapl-break-200
```
