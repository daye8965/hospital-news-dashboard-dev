# hospital-news-dashboard-dev (개발)

서울아산병원 홍보팀 언론Unit의 **병원 뉴스 대시보드 개발 버전**입니다.
신규 기능은 전부 여기서 만들고, 검증된 것만 운영 저장소로 옮깁니다.

- 공개 주소: https://daye8965.github.io/hospital-news-dashboard-dev/
- 운영 버전: `../hospital-news-dashboard` (별도 저장소 `hospital-news-dashboard`)

## 운영본과의 차이 (2026-09 기준)

dev에만 있는 것:

- `enrich_articles.py` — 기사 보강 (기자명·기자이메일·지면·출입기자)
- `.github/workflows/enrich.yml` — 보강 단독 실행용 (평소엔 수집 워크플로 안에서 같이 돌아감)
- 대시보드 탭 `paper`(🗞️ 지면모음), `report`(📑 서울아산병원-보고용)
- 헤더 아래 개발 버전 안내 문구

운영본 탭은 `ours`/`others`/`compare` 3개뿐입니다.

## 구조

| 파일 | 역할 |
|---|---|
| `naver_to_csv.py` | 네이버 뉴스 API 수집. 병원별 검색어, 제외 키워드/매체, 매체명 정규화, 중복 제거 |
| `enrich_articles.py` | 기사 본문·언론사 원문에서 기자명·지면 추출, 구글 시트의 출입기자 명단과 대조 |
| `clean_csv.py` | 수집 전 CSV 오염 행 정리 |
| `generate_weekly_report.py` | 주간 보고서 생성 — 매주 월요일 |
| `docs/index.html` | 대시보드 본체 (단일 파일, CSV를 클라이언트에서 읽어 렌더링) |
| `docs/news.csv` | 수집 데이터. **사람이 직접 편집하지 말 것** |
| `docs/enrich_status.json` | 보강 진행 상태 |

## 기능의 배경

지금의 탭 구성은 아래 요구사항에서 나왔습니다. 관련 작업을 할 때 의도를 참고하세요.

- **지면모음(`paper`)** — 온라인 기사 본문에 `A17면` 같은 지면 표기가 있는 경우가 있어, 이를 근거로 지면 기사만 따로 모읍니다.
- **보고용(`report`)** — 따로 집계하는 「주요병원 연간언론실적」 리스트와 일치하는 매체만 추려 보는 탭입니다. 대시보드가 수집하지만 실적에서 제외되는 기사(제약·지자체·타기관 행사·연예인 관련)를 걸러내려는 목적입니다.
- **출입기자 표시** — 기사 바이라인이 우리 출입기자면 옆에 표시합니다. 명단은 구글 시트(`기자리스트` 탭)에 있고 수시로 갱신되며, **크롤링 시점 기준**으로 판정합니다.

## 데이터

`docs/news.csv` 헤더 (순서 고정):

```
날짜,병원그룹,검색어,매체,제목,교수명,요약,언론사원문,네이버링크,발행일시,수집일시,기자명,기자이메일,지면,출입기자
```

약 17,000행. 전체를 읽지 말고 `head`/`grep`/`awk`로 필요한 부분만 보세요.

## 자동화

- 스케줄: `cron "20 19 * * *"`, `"17 23 * * *"` (UTC) — GitHub 예약 실행이 1~2시간 밀리는 것을 감안해 **앞당겨 설정**해 둔 값입니다. 시간 조정 시 이 지연을 고려하세요.
- 매주 월요일 `cron "0 0 * * 1"`에는 수집 대신 주간 보고서를 만듭니다.
- `concurrency: news-data` — 수집과 보강이 같은 CSV를 건드리므로 동시 실행을 막아 둡니다.
- 보강 단계는 `continue-on-error: true` — **보강이 실패해도 수집한 기사는 반드시 커밋**되도록 한 의도적 설계입니다.
- 커밋 충돌 시 이번 결과를 버리고 정상 종료하며, 빠진 날짜는 다음 실행의 백필이 다시 수집합니다 (`MAX_BACKFILL_DAYS = 7`).

## Secrets

`NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`,
`GOOGLE_SERVICE_ACCOUNT_JSON`, `REPORTER_SHEET_ID`, `REPORTER_SHEET_RANGE`, `REPORTER_SHEET_CSV_URL`

## 작업 규칙

- 커밋 메시지는 기존 히스토리를 따릅니다 — 한글 서술형 또는 영문 명령형 한 줄.
- 워크플로의 `자동 수집 …` 커밋과 섞이므로 push 전에 항상 `git pull --rebase`.
- 운영 반영은 이 세션에서 하지 않습니다. dev에서 검증을 끝낸 뒤 운영 세션에서 옮기세요.
- 정적 페이지 확인: `python -m http.server 8765` 후 `http://localhost:8765/docs/`
