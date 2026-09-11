"""
news.csv 오염 데이터 정리 스크립트
"""
import csv, re, sys
from pathlib import Path

CSV_PATH = Path("docs/news.csv")

REMOVE_KEYWORDS = {
    "삼성병원", "연대병원", "서울중앙병원",
    # 타병원
    "강북삼성병원", "분당서울대병원", "강남세브란스", "영동세브란스",
    "용인세브란스", "보라매병원", "세란병원", "강남베드로병원",
    "순천향대서울병원", "원주의료원", "소방병원", "서울의료원",
    "경기도의료원", "한림대성심병원", "고신대복음병원", "삼육보건대",
    # 연세유업
    "연세유업", "세브란스케어",
    # 의대 입시
    "지역의사제", "수능최저", "수시 선발", "의대 입시", "편입학",
    # 제약 홍보
    "ESG 보고서", "주요공시", "개장 전 주요", "제약 단신", "제약 브리핑",
    "제약공시", "바이오 브리핑", "제약소식", "제약업계 소식",
    "심포지엄 성료", "MOU 체결",
    # 부동산
    "임대주택", "재건축", "스마트도시 조성", "복합문화중심도시", "배곧",
    # 범죄
    "살인미수", "스토킹 살인", "살해미수",
    # 지자체
    "왕진버스", "맨발걷기",
}

REMOVE_PATTERN = re.compile(
    r"(강북삼성|분당서울대|강남세브란스|영동세브란스|용인세브란스"
    r"|보라매병원|세란병원|강남베드로|삼육보건대"
    r"|연세유업|세브란스케어"
    r"|지역의사제|수능최저|의대\s*입시|수시\s*선발|편입학"
    r"|주요\s*공시|개장\s*전\s*주요|제약\s*단신|제약\s*브리핑"
    r"|살인미수|스토킹\s*살인|살해미수|징역\s*\d+년"
    r"|속옷\s*훔|여성\s*속옷|도어록.*외워|옆집\s*침입|비밀번호\s*외워"
    r"|왕진버스|맨발걷기"
    r"|삼성전자|삼성증권|삼성바이오로직스"
    r"|LG화학|GC녹십자|대원제약|한미사이언스|에임드바이오"
    r"|임대주택|재건축|스마트도시\s*조성|복합문화중심도시)"
)

def title_key(t):
    """제목 정규화: 특수문자·따옴표·공백·말줄임표 모두 제거 후 앞 30자"""
    t = re.sub(r"[^\w가-힣a-zA-Z0-9]", "", t)
    return t.lower()[:30]  # 앞 30자만 비교 (말미 차이 무시)

# CSV 파일 없으면 조용히 종료
if not CSV_PATH.exists():
    print(f"파일 없음: {CSV_PATH} — 건너뜀")
    sys.exit(0)

# 파일 읽기
with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
    content = f.read().strip()

# 빈 파일이면 종료
if not content:
    print("CSV 파일이 비어있음 — 건너뜀")
    sys.exit(0)

rows, removed = [], 0
seen_titles = set()
seen_urls   = set()
fieldnames  = None

with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames

    if not fieldnames:
        print("헤더 없음 — 건너뜀")
        sys.exit(0)

    for row in reader:
        keyword = row.get("검색어", "")
        title   = row.get("제목", "")
        summary = row.get("요약", "")
        tk  = title_key(title)
        url = (row.get("언론사원문","") or row.get("네이버링크","")).strip()

        if keyword in REMOVE_KEYWORDS:
            removed += 1; continue
        if REMOVE_PATTERN.search(title + summary):
            removed += 1; continue
        if url and url in seen_urls:
            removed += 1; continue
        if tk and tk in seen_titles:
            removed += 1; continue

        if url: seen_urls.add(url)
        if tk:  seen_titles.add(tk)

        # 구버전 행 보정: 발행일시 없으면 날짜 컬럼으로 채우기
        if not row.get("발행일시","").strip():
            row["발행일시"] = row.get("날짜","")
        # 병원그룹 없으면 검색어로 유추
        if not row.get("병원그룹","").strip():
            kw = row.get("검색어","")
            if kw in ("서울아산병원","아산병원","울산의대"):
                row["병원그룹"] = "아산"
            elif kw in ("삼성서울병원","성대의대","성균관의대"):
                row["병원그룹"] = "삼성"
            elif kw in ("세브란스병원","연세암병원","연세대병원"):
                row["병원그룹"] = "세브란스"
            elif kw in ("서울대병원","서울대학교병원","서울의대"):
                row["병원그룹"] = "서울대"
            elif kw in ("서울성모병원","카톨릭성모병원"):
                row["병원그룹"] = "성모"
        # 교수명 컬럼 없으면 빈값 추가
        if "교수명" not in row:
            row["교수명"] = ""

        rows.append(row)

# 신버전 컬럼 순서로 통일해서 저장
NEW_FIELDNAMES = ["날짜","병원그룹","검색어","매체","제목","교수명","요약","언론사원문","네이버링크","발행일시","수집일시"]

with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=NEW_FIELDNAMES, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)

print(f"정리 완료: {removed}건 제거 → 남은 데이터: {len(rows)}건")
