import os
import csv
import requests
import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ── API 키 ────────────────────────────────────────────────────────────────────
CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID",     "")
CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

# ── 병원별 검색 키워드 설정 ────────────────────────────────────────────────────
HOSPITAL_QUERIES = {
    "아산":    ["서울아산병원", "아산병원", "울산의대", "울산대의대"],
    "삼성":    ["삼성서울병원", "삼성병원", "성대의대", "성균관의대"],
    "세브란스": ["세브란스병원", "연세암병원", "연대병원", "연세대병원"],
    "서울대":  ["서울대병원", "서울대학교병원", "서울의대"],
    "성모":    ["서울성모병원", "카톨릭성모병원", "성모병원"],
}

EXCLUDE_KEYWORDS = {"부고", "부음", "별세"}
CSV_PATH = Path("docs/news.csv")

KST         = timezone(timedelta(hours=9))
today       = datetime.now(KST).date()
target_date = today - timedelta(days=1)
start_dt    = datetime(target_date.year, target_date.month, target_date.day, tzinfo=KST)
end_dt      = datetime(today.year,       today.month,       today.day,       tzinfo=KST)

FIELDNAMES = ["날짜", "병원그룹", "검색어", "매체", "제목", "교수명", "요약", "언론사원문", "네이버링크", "발행일시", "수집일시"]

# 도메인 → 매체명 매핑 (자주 등장하는 주요 언론사)
MEDIA_MAP = {
    "chosun.com":         "조선일보",
    "joongang.co.kr":     "중앙일보",
    "donga.com":          "동아일보",
    "hani.co.kr":         "한겨레",
    "khan.co.kr":         "경향신문",
    "ohmynews.com":       "오마이뉴스",
    "yna.co.kr":          "연합뉴스",
    "yonhapnewstv.co.kr": "연합뉴스TV",
    "newsis.com":         "뉴시스",
    "news1.kr":           "뉴스1",
    "edaily.co.kr":       "이데일리",
    "etnews.com":         "전자신문",
    "mt.co.kr":           "머니투데이",
    "hankyung.com":       "한국경제",
    "mk.co.kr":           "매일경제",
    "sedaily.com":        "서울경제",
    "news.kbs.co.kr":     "KBS",
    "imbc.com":           "MBC",
    "news.sbs.co.kr":     "SBS",
    "jtbc.co.kr":         "JTBC",
    "ytn.co.kr":          "YTN",
    "mbn.co.kr":          "MBN",
    "tvchosun.com":       "TV조선",
    "healthchosun.com":   "헬스조선",
    "kormedi.com":        "코메디닷컴",
    "medicaltimes.com":   "메디칼타임즈",
    "doctorsnews.co.kr":  "의사신문",
    "rapportian.com":     "라포르시안",
    "mdtoday.co.kr":      "메디컬투데이",
    "bosa.co.kr":         "보건복지부",
    "newsmp.com":         "메디컬포스트",
}

print(f"수집 기간: {start_dt} ~ {end_dt}")


# ── 유틸 ─────────────────────────────────────────────────────────────────────
def clean_text(text: str) -> str:
    return html.unescape(re.sub(r"<.*?>", "", text)).strip()

def extract_media(url: str) -> str:
    """URL에서 매체명 추출. 매핑 없으면 도메인 그대로 반환."""
    if not url:
        return ""
    try:
        domain = re.sub(r"https?://(www\.)?", "", url).split("/")[0]
        for key, name in MEDIA_MAP.items():
            if key in domain:
                return name
        # 매핑 없으면 도메인 앞부분만 깔끔하게
        parts = domain.split(".")
        return parts[-2] if len(parts) >= 2 else domain
    except Exception:
        return ""

def is_excluded(item: dict) -> bool:
    combined = clean_text(item.get("title", "")) + clean_text(item.get("description", ""))
    return any(kw in combined for kw in EXCLUDE_KEYWORDS)

def dedup_key(item: dict) -> str:
    return item.get("originallink") or item.get("link", "")


# ── 단일 키워드 수집 ──────────────────────────────────────────────────────────
def collect_news(query: str) -> list[dict]:
    collected, now = [], datetime.now(KST)
    for start in range(1, 1001, 100):
        resp = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={"X-Naver-Client-Id": CLIENT_ID, "X-Naver-Client-Secret": CLIENT_SECRET},
            params={"query": query, "display": 100, "start": start, "sort": "date"},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            break
        for item in items:
            pub_dt = parsedate_to_datetime(item["pubDate"]).astimezone(KST)
            if start_dt <= pub_dt < end_dt and not is_excluded(item):
                orig = item.get("originallink", "")
                collected.append({
                    "날짜":      str(target_date),
                    "검색어":    query,
                    "매체":      extract_media(orig),
                    "제목":      clean_text(item.get("title", "")),
                    "교수명":    "",
                    "요약":      clean_text(item.get("description", "")),
                    "언론사원문": item.get("originallink", ""),
                    "네이버링크": item.get("link", ""),
                    "발행일시":  pub_dt.strftime("%Y-%m-%d %H:%M"),
                    "수집일시":  now.strftime("%Y-%m-%d %H:%M"),
                })
        last_pub = parsedate_to_datetime(items[-1]["pubDate"]).astimezone(KST)
        if last_pub < start_dt:
            break
    return collected


# ── 병원 그룹별 수집 + 교차 중복 제거 ────────────────────────────────────────
def collect_all() -> list[dict]:
    all_items = []
    for group, queries in HOSPITAL_QUERIES.items():
        seen = set()
        group_total = 0
        for query in queries:
            news = collect_news(query)
            added = 0
            for item in news:
                key = dedup_key(item) or item["제목"]
                if key not in seen:
                    seen.add(key)
                    item["병원그룹"] = group
                    all_items.append(item)
                    added += 1
            group_total += added
            print(f"  [{group}] {query}: 수집 {len(news)}건 → 신규 {added}건")
        print(f"  ▶ [{group}] 합계: {group_total}건\n")
    return all_items


# ── CSV 저장 (누적) ────────────────────────────────────────────────────────────
def save_to_csv(items: list[dict]) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(items)
    print(f"CSV 저장 완료: {CSV_PATH} ({len(items)}행 추가)")


if __name__ == "__main__":
    news = collect_all()
    print(f"\n총 {len(news)}건 수집 (중복 제거 후)")
    if news:
        save_to_csv(news)
    else:
        print("저장할 기사 없음")
