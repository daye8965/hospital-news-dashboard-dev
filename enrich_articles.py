"""
기사 보강 스크립트 — 뉴스 수집 뒤에 실행

1) 기자명·기자이메일 : 네이버 기사 페이지 바이라인 (네이버 링크가 없으면 언론사 원문)
2) 지면              : 네이버 '신문 지면 보기' 페이지의 면 정보 (주요 신문 19곳)
3) 출입기자          : 구글 시트 출입기자 명단으로 CSV 전체를 매 실행마다 다시 대조

출입기자 명단 자체는 어디에도 저장하지 않고, 기사별 표시(Y)만 CSV에 남긴다.
"""
import csv
import html
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

CSV_PATH    = Path("docs/news.csv")
STATUS_PATH = Path("docs/enrich_status.json")

FIELDNAMES = ["날짜","병원그룹","검색어","매체","제목","교수명","요약","언론사원문","네이버링크",
              "발행일시","수집일시","기자명","기자이메일","지면","출입기자"]

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)

# 과거 기사 백필은 한 번에 끝내지 않고 실행마다 나눠서 진행한다
BYLINE_BUDGET  = int(os.environ.get("BYLINE_BUDGET", "800"))       # 실행당 기사 페이지 요청 수
PAPER_BUDGET   = int(os.environ.get("PAPER_BUDGET", "300"))        # 실행당 지면 페이지 요청 수
TIME_LIMIT_SEC = int(os.environ.get("ENRICH_TIME_LIMIT", "1500"))  # 전체 보강 시간 상한
REQUEST_DELAY  = 0.25
# 지면 정보는 신문 발행 뒤에 붙으므로, 이 기간 안의 기사는 '미게재'여도 다시 확인한다
PAPER_RECHECK_DAYS = 4

CHECKED_NONE = "-"   # 확인했지만 값이 없음 → 다음 실행에서 다시 요청하지 않음

# 네이버 '신문 지면 보기'를 제공하는 신문사 (네이버 언론사 코드)
PAPER_PRESS = {
    "023": "조선일보",   "020": "동아일보",   "025": "중앙일보",   "469": "한국일보",
    "021": "문화일보",   "081": "서울신문",   "005": "국민일보",   "022": "세계일보",
    "028": "한겨레",     "032": "경향신문",   "009": "매일경제",   "015": "한국경제",
    "011": "서울경제",   "016": "헤럴드경제", "008": "머니투데이", "014": "파이낸셜뉴스",
    "277": "아시아경제", "018": "이데일리",   "030": "전자신문",
}

NAVER_ARTICLE_RE = re.compile(r"n\.news\.naver\.com/(?:mnews/)?article/(?:newspaper/)?(\d{3})/(\d{10})")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

SURNAMES = set("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구나민진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부가복태목계피두감음빈동온호")
TITLE_SUFFIXES = ("기자", "특파원", "위원", "에디터", "앵커", "뉴스팀", "취재팀", "편집국", "보도국")
NOT_NAMES = {"의사", "약사", "교수", "박사", "변호사", "한의사", "인턴", "객원", "선임", "수석",
             "온라인", "디지털", "사진", "영상", "그래픽", "편집", "종합", "특별", "공동", "정리", "취재"}
MEDIA_HINT_RE = re.compile(r"일보|신문|뉴스|경제|방송|닷컴|미디어|타임즈|타임스|저널|통신|데일리|투데이|헬스|메디|TV|http|\.com|\.kr", re.I)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
})


class Blocked(Exception):
    """접근 차단(403/429) — 이번 실행에서 해당 호스트 요청을 멈춘다"""


def fetch(url):
    """200이면 본문, 그 밖의 응답이면 None. 차단 응답은 Blocked"""
    time.sleep(REQUEST_DELAY)
    resp = SESSION.get(url, timeout=15)
    if resp.status_code in (403, 429):
        raise Blocked(f"{resp.status_code} {url}")
    if resp.status_code != 200:
        return None
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return resp.text


def _clean(fragment):
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).strip()


def _add_unique(target, items):
    for item in items:
        if item and item not in target:
            target.append(item)


def _pub_date(row):
    try:
        return datetime.strptime((row.get("날짜") or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ── 1) 기자명 ────────────────────────────────────────────────────────────────
def parse_byline(raw):
    """바이라인 문자열에서 기자 이름과 이메일을 뽑는다.
    '고양=박재구 기자 park9@kmib.co.kr'          → (['박재구'], ['park9@kmib.co.kr'])
    '최지은·문일요·박선하 기자'                   → (['최지은', '문일요', '박선하'], [])
    '이진한 의학전문기자·의사 likeday@donga.com'  → (['이진한'], ['likeday@donga.com'])"""
    text = _clean(raw)
    emails = [e.lower() for e in EMAIL_RE.findall(text)]
    text = EMAIL_RE.sub(" ", text)
    text = re.sub(r"[^\s=]{1,10}\s*=\s*", " ", text)   # 지역 표기 '세종=' 제거
    names = []
    for token in re.split(r"[\s·,/|()\[\]<>{}]+", text):
        if not re.fullmatch(r"[가-힣]{2,4}", token):
            continue
        if token in NOT_NAMES or token.endswith(TITLE_SUFFIXES) or token[0] not in SURNAMES:
            continue
        if MEDIA_HINT_RE.search(token):
            continue
        _add_unique(names, [token])
    return names, emails


def byline_from_naver(page):
    names, emails = [], []
    for fragment in re.findall(r'class="byline_s"[^>]*>(.*?)</span>', page, re.S):
        found_names, found_emails = parse_byline(fragment)
        _add_unique(names, found_names)
        _add_unique(emails, found_emails)
    if not names:
        for fragment in re.findall(r'class="media_end_head_journalist_name"[^>]*>(.*?)</em>', page, re.S):
            _add_unique(names, parse_byline(fragment)[0])
    return names, emails


META_AUTHOR_RE = re.compile(
    r'<meta\b[^>]*?(?:property|name)\s*=\s*["\'](?:article:author|og:article:author|dable:author|author)["\'][^>]*>',
    re.I)
META_CONTENT_RE = re.compile(r'content\s*=\s*["\']([^"\']*)["\']', re.I)
BODY_BYLINE_RE = re.compile(
    r"([가-힣]{2,4})\s*(?:선임|수석|의학전문|전문|객원|인턴)?기자\s*[(\[]?\s*"
    r"([A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


def byline_from_original(page):
    """언론사 원문: 기사 전용 메타태그 우선, 없으면 본문의 '홍길동 기자 (메일)' 형식"""
    names, emails = [], []
    for tag in META_AUTHOR_RE.findall(page):
        match = META_CONTENT_RE.search(tag)
        value = html.unescape(match.group(1)).strip() if match else ""
        if not value or MEDIA_HINT_RE.search(value):
            continue   # 매체명·URL이 들어간 메타태그는 기자 정보가 아님
        found_names, found_emails = parse_byline(value)
        _add_unique(names, found_names)
        _add_unique(emails, found_emails)
    if not names:
        match = BODY_BYLINE_RE.search(_clean(page))
        if match:
            _add_unique(names, parse_byline(match.group(1))[0])
            _add_unique(emails, [match.group(2).lower()])
    return names, emails


def enrich_bylines(rows, status, deadline):
    targets = [r for r in rows if not (r.get("기자명") or "").strip()]
    targets.sort(key=lambda r: r.get("발행일시") or r.get("날짜") or "", reverse=True)
    stats = {"대상": len(targets), "요청": 0, "채움": 0, "이름없음": 0, "오류": 0, "차단호스트": 0}
    cache, blocked_hosts = {}, set()

    for row in targets:
        if stats["요청"] >= BYLINE_BUDGET or time.monotonic() > deadline:
            break
        match = NAVER_ARTICLE_RE.search(row.get("네이버링크") or "")
        if match:
            url = f"https://n.news.naver.com/mnews/article/{match.group(1)}/{match.group(2)}"
            parser = byline_from_naver
        elif (row.get("언론사원문") or "").startswith("http"):
            url, parser = row["언론사원문"], byline_from_original
        else:
            row["기자명"] = CHECKED_NONE
            continue

        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        if host in blocked_hosts:
            continue
        if url not in cache:
            stats["요청"] += 1
            try:
                page = fetch(url)
            except Blocked:
                blocked_hosts.add(host)
                stats["차단호스트"] = len(blocked_hosts)
                continue
            except requests.RequestException:
                stats["오류"] += 1   # 일시 오류는 비워 두고 다음 실행에서 재시도
                continue
            cache[url] = parser(page) if page else ([], [])

        names, emails = cache[url]
        if names:
            row["기자명"] = ", ".join(names)
            stats["채움"] += 1
        else:
            row["기자명"] = CHECKED_NONE
            stats["이름없음"] += 1
        row["기자이메일"] = ", ".join(emails)

    stats["남은대상"] = sum(1 for r in rows if not (r.get("기자명") or "").strip())
    status["기자명"] = stats


# ── 2) 지면 ──────────────────────────────────────────────────────────────────
def fetch_paper_page(oid, day):
    """신문 지면 페이지 → {기사ID: 'A12면'}. 발행이 없는 날은 빈 dict, 요청 실패는 None"""
    ymd = day.strftime("%Y%m%d")
    page = fetch(f"https://media.naver.com/press/{oid}/newspaper?date={ymd}")
    if page is None:
        return None
    link_re = re.compile(rf"/article/newspaper/{oid}/(\d{{10}})(?:\?date=(\d{{8}}))?")
    result = {}
    # 면마다 page_notation('<em>A1</em>면') 제목 뒤에 그 면의 기사 링크가 이어진다
    for chunk in page.split('class="page_notation"')[1:]:
        match = re.match(r"[^>]*>(.*?)</span>", chunk, re.S)
        label = re.sub(r"\s+", "", _clean(match.group(1))) if match else ""
        if not re.fullmatch(r"[A-Z]?\d{1,3}면", label):
            continue
        for aid, link_date in link_re.findall(chunk):
            if link_date and link_date != ymd:
                continue   # 해당 날짜 호가 없어 다른 날짜 지면이 나온 경우
            result.setdefault(aid, label)
    return result


def enrich_paper(rows, status, deadline):
    today = NOW.date()
    candidates = []
    for row in rows:
        current = (row.get("지면") or "").strip()
        if current and current != CHECKED_NONE:
            continue
        match = NAVER_ARTICLE_RE.search(row.get("네이버링크") or "")
        if not match or match.group(1) not in PAPER_PRESS:
            continue
        day = _pub_date(row)
        if not day:
            continue
        if current == CHECKED_NONE and (today - day).days > PAPER_RECHECK_DAYS:
            continue
        candidates.append((row, match.group(1), match.group(2), day))

    def issue_keys(oid, day):
        # 온라인 기사는 전날 저녁에 먼저 나오는 경우가 많아 발행일과 다음 날 지면을 함께 본다
        return [(oid, d) for d in (day, day + timedelta(days=1)) if d <= today]

    needed = sorted({key for _, oid, _, day in candidates for key in issue_keys(oid, day)},
                    key=lambda key: key[1], reverse=True)
    stats = {"대상": len(candidates), "요청": 0, "게재확인": 0, "미게재": 0, "오류": 0}
    pages = {}
    for key in needed:
        if stats["요청"] >= PAPER_BUDGET or time.monotonic() > deadline:
            break
        stats["요청"] += 1
        try:
            pages[key] = fetch_paper_page(*key)
        except Blocked:
            stats["차단"] = True
            break
        except requests.RequestException:
            stats["오류"] += 1

    for row, oid, aid, day in candidates:
        keys = issue_keys(oid, day)
        label = next((pages[k][aid] for k in keys if pages.get(k) and aid in pages[k]), "")
        if label:
            row["지면"] = label
            stats["게재확인"] += 1
        elif all(pages.get(k) is not None for k in keys):
            row["지면"] = CHECKED_NONE
            stats["미게재"] += 1

    status["지면"] = stats


# ── 3) 출입기자 ──────────────────────────────────────────────────────────────
def _norm(text):
    return re.sub(r"[\s()·\-_.]", "", text or "").lower()


def _find_col(header, keys):
    for i, cell in enumerate(header):
        if any(key in _norm(str(cell)) for key in keys):
            return i
    return None


def parse_roster(values):
    header, header_idx, name_col = [], 0, None
    for header_idx, header in enumerate(values[:10]):
        name_col = _find_col(header, ("이름", "성명", "기자명"))
        if name_col is not None:
            break
    if name_col is None:
        raise ValueError("명단에서 이름 열을 찾지 못함 (헤더에 '이름'·'성명'·'기자명' 중 하나 필요)")
    media_col = _find_col(header, ("매체", "언론사", "소속", "회사"))
    email_col = _find_col(header, ("이메일", "메일", "email"))

    emails, by_name = set(), {}
    for row in values[header_idx + 1:]:
        def cell(i):
            return str(row[i]).strip() if i is not None and i < len(row) else ""
        emails.update(e.lower() for e in EMAIL_RE.findall(cell(email_col)))
        name = re.sub(r"\s", "", cell(name_col))
        if re.fullmatch(r"[가-힣]{2,4}", name):
            by_name.setdefault(name, set()).add(_norm(cell(media_col)))
    return {"emails": emails, "by_name": by_name, "has_media": media_col is not None}


def load_roster():
    """서비스 계정(권장) 또는 웹 게시 CSV 주소로 명단을 읽는다. 둘 다 없으면 None"""
    sa_json     = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    sheet_id    = os.environ.get("REPORTER_SHEET_ID", "").strip()
    sheet_range = os.environ.get("REPORTER_SHEET_RANGE", "").strip() or "A:Z"
    csv_url     = os.environ.get("REPORTER_SHEET_CSV_URL", "").strip()

    if sa_json and sheet_id:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        creds = service_account.Credentials.from_service_account_info(
            json.loads(sa_json), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(sheet_range, safe='')}"
        resp = AuthorizedSession(creds).get(url, timeout=20)
        resp.raise_for_status()
        values = resp.json().get("values", [])
    elif csv_url:
        resp = requests.get(csv_url, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        values = list(csv.reader(resp.text.splitlines()))
    else:
        return None
    return parse_roster(values)


def is_beat_reporter(row, roster):
    row_emails = {e.lower() for e in EMAIL_RE.findall(row.get("기자이메일") or "")}
    if row_emails & roster["emails"]:
        return True
    media = _norm(row.get("매체"))
    for name in re.split(r"[,\s]+", row.get("기자명") or ""):
        roster_media = roster["by_name"].get(name)
        if not roster_media:
            continue
        if not roster["has_media"]:
            return True
        # 동명이인 방지: 명단의 매체와 기사의 매체가 같아야 인정
        if media and any(m and (m in media or media in m) for m in roster_media):
            return True
    return False


def mark_beat_reporters(rows, status):
    try:
        roster = load_roster()
    except Exception as e:   # 명단 문제로 기자명·지면 보강 결과까지 잃지 않도록 여기서 끝낸다
        code = getattr(getattr(e, "response", None), "status_code", "")
        status["출입기자"] = {"결과": f"명단 불러오기 실패 ({type(e).__name__} {code}".strip() + ") — 기존 표시 유지"}
        return
    if roster is None:
        status["출입기자"] = {"결과": "명단 미설정 — 기존 표시 유지"}
        return
    marked = 0
    for row in rows:
        row["출입기자"] = "Y" if is_beat_reporter(row, roster) else ""
        marked += row["출입기자"] == "Y"
    status["출입기자"] = {"결과": "대조 완료", "명단인원": len(roster["by_name"]), "표시기사": marked}


# ── 실행 ─────────────────────────────────────────────────────────────────────
def read_rows():
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(rows):
    tmp = CSV_PATH.with_name(CSV_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore", restval="")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(CSV_PATH)


def write_status(status):
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def main(status):
    if not CSV_PATH.exists():
        status["결과"] = "CSV 없음 — 건너뜀"
        return
    rows = read_rows()
    status["전체기사"] = len(rows)
    deadline = time.monotonic() + TIME_LIMIT_SEC

    enrich_bylines(rows, status, deadline)
    enrich_paper(rows, status, deadline)
    mark_beat_reporters(rows, status)

    write_rows(rows)
    status["결과"] = "완료"


if __name__ == "__main__":
    run_status = {"실행시각": NOW.strftime("%Y-%m-%d %H:%M")}
    try:
        main(run_status)
    except Exception:
        run_status["결과"] = "실패"
        run_status["오류"] = traceback.format_exc(limit=5)
        write_status(run_status)
        print(run_status["오류"], file=sys.stderr)
        sys.exit(1)
    write_status(run_status)
    print(json.dumps(run_status, ensure_ascii=False, indent=2))
