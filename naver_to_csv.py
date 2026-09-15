import os, csv, html, re
import requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

#test
# ── API 키 ────────────────────────────────────────────────────────────────────
CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID",     "")
CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

# ── 병원별 검색 키워드 ────────────────────────────────────────────────────────
HOSPITAL_QUERIES = {
    "아산":    ["서울아산병원", "아산병원"],
    # 울산의대 제거 → 의대입시/지역의사제/울산범죄 노이즈 주범
    # 성대의대/성균관의대 제거 → 삼성병원 제거 → 타병원/제약 노이즈
    "삼성":    ["삼성서울병원", "성균관의대"],
    # 연대병원/연세대병원 제거 → 무관 기사 유입
    "세브란스": ["세브란스병원", "연세암병원"],
    "서울대":  ["서울대학교병원", "서울대병원"],
    # 서울의대 제거 → "서울 의과대학 의대생" 등 무관 기사 대량 유입
    # 서울대학교병원 키워드로 충분히 커버됨
    "성모":    ["서울성모병원"],
}

# ── 제외 키워드 (제목+요약에 포함 시 수집 제외) ──────────────────────────────
EXCLUDE_KEYWORDS = {
    # 부고
    "부고", "부음", "별세", "타계", "빈소", "발인", "영결식",

    # 삼성 계열사 (병원 무관)
    "삼성전자", "삼성증권", "삼성물산", "삼성SDI", "삼성SDS",
    "삼성화재", "삼성생명", "삼성바이오",

    # 타병원 — 빅5 아닌 병원 (① 타병원 기사)
    "강북삼성병원", "분당서울대병원", "강남세브란스", "영동세브란스", "국제성모병원","인천성모병원","부천성모병원",
    "용인세브란스", "보라매병원", "세란병원", "강남베드로병원",
    "순천향대서울병원", "원주의료원", "소방병원", "서울의료원",
    "경기도의료원", "한림대성심병원", "고신대복음병원", "삼육보건대", "OK치과",

    # 연세유업 브랜드 (⑤ 세브란스 무관 브랜드)
    "연세유업", "세브란스케어",

    # 의대 입시/지역의사제 (④)
    "지역의사제", "수능최저", "수시 선발", "정시 선발", "의대 입시",
    "수시로 뽑", "편입학", "사주 컨설팅", "입결",

    # 제약/바이오 단순 홍보 (②)
    "ESG 보고서", "주요공시", "개장 전 주요", "제약 단신", "제약 브리핑",
    "제약공시", "바이오 브리핑", "제약소식", "제약업계 소식",
    "심포지엄 성료", "학술 심포지엄 개최", "MOU 체결", "업무협약 체결",

    # 부동산/지역개발 (③)
    "분양", "임대주택", "재건축", "스마트도시 조성", "복합문화중심도시",
    "배곧", "시흥시",

    # 범죄/사건사고 (⑥)
    "살인미수", "스토킹 살인", "살해미수",

    # 지자체/보건소 행사 (⑧)
    "왕진버스", "맨발걷기", "보건의료상생", "찾아가는 의료",

    # 주요공시/증권 (⑨)
    "주요공시", "개장 전",

    # 인물/브랜드/기타 노이즈
    "김우빈", "김희선", "김동일", "파크로쉬", "추도사",
    "장미화", "홍수환", "조우신", "레몬헬스케어", "GSK", "보령시장",
    "돌싱", "모친상", "부친상", "장례식장", "셀럽포토",
    "옥희", "서울시스터즈",
}

# ── 제외 매체 (이 매체의 기사는 수집 제외) ──────────────────────────────────
EXCLUDE_MEDIA = {"celuvmedia"}

# ── 제외 패턴 (정규식 — 위 키워드로 못 잡는 케이스 보완) ─────────────────────
EXCLUDE_PATTERN = re.compile(
    r"(강북삼성|분당서울대|강남세브란스|영동세브란스|용인세브란스"
    r"|보라매병원|세란병원|강남베드로|삼육보건대"
    r"|연세유업|세브란스케어"
    r"|지역의사제|수능최저|의대\s*입시|수시\s*선발|편입학"
    r"|주요\s*공시|개장\s*전\s*주요|제약\s*단신|제약\s*브리핑|바이오\s*브리핑"
    r"|살인미수|스토킹\s*살인|살해미수|징역\s*\d+년"
    r"|속옷\s*훔|여성\s*속옷|도어록.*외워|옆집\s*침입|비밀번호\s*외워"
    r"|왕진버스|맨발걷기"
    r"|삼성전자|삼성증권|삼성바이오로직스"
    r"|LG화학|GC녹십자|대원제약|한미사이언스|에임드바이오"
    r"|분양\s*[0-9]|임대주택|재건축|스마트도시\s*조성|복합문화중심도시)"
)

CSV_PATH   = Path("docs/news.csv")
FIELDNAMES = ["날짜","병원그룹","검색어","매체","제목","교수명","요약","언론사원문","네이버링크","발행일시","수집일시",
              "기자명","기자이메일","지면","출입기자"]   # 뒤쪽 4개는 enrich_articles.py가 채움

KST         = timezone(timedelta(hours=9))
today       = datetime.now(KST).date()
target_date = today - timedelta(days=1)

MAX_BACKFILL_DAYS = 7   # 네이버 API가 되짚을 수 있는 현실적 한계

def _resolve_start_date():
    """CSV의 마지막 수집일 다음 날부터 수집.
    스케줄 실행이 지연·누락돼도 빠진 날짜를 다음 실행에서 자동으로 메운다."""
    earliest = target_date - timedelta(days=MAX_BACKFILL_DAYS - 1)
    if not CSV_PATH.exists():
        return target_date
    last = None
    try:
        with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                d = (row.get("날짜") or "")[:10]
                if len(d) == 10 and (last is None or d > last):
                    last = d
    except Exception as e:
        print(f"CSV 마지막 날짜 확인 실패({e}) — 어제만 수집")
        return target_date
    if not last:
        return target_date
    nxt = datetime.strptime(last, "%Y-%m-%d").date() + timedelta(days=1)
    return max(min(nxt, target_date), earliest)

start_date = _resolve_start_date()
start_dt   = datetime(start_date.year, start_date.month, start_date.day, tzinfo=KST)
end_dt     = datetime(today.year,      today.month,      today.day,      tzinfo=KST)

# ── 매체 매핑 (출입기자리스트 기반) ──────────────────────────────────────────
MEDIA_MAP = {
    # 종합 일간지
    "chosun.com":"조선일보", "joongang.co.kr":"중앙일보", "joins.com":"중앙일보",
    "news.joins.com":"중앙일보", "donga.com":"동아일보", "hankookilbo.com":"한국일보",
    "hani.co.kr":"한겨레", "khan.co.kr":"경향신문", "kyunghyang.com":"경향신문",
    "seoul.co.kr":"서울신문", "kmib.co.kr":"국민일보", "segye.com":"세계일보",
    "munhwa.com":"문화일보", "naeil.com":"내일신문",
    # 경제지
    "hankyung.com":"한국경제", "mk.co.kr":"매일경제", "mkhealth.co.kr":"매경헬스",
    "sedaily.com":"서울경제", "fnnews.com":"파이낸셜뉴스", "mt.co.kr":"머니투데이",
    "heraldcorp.com":"헤럴드경제", "heraldm.com":"헤럴드경제",
    "asiae.co.kr":"아시아경제", "edaily.co.kr":"이데일리", "etnews.com":"전자신문",
    "ajunews.com":"아주경제", "chosunbiz.com":"조선비즈", "etoday.co.kr":"이투데이",
    "dt.co.kr":"디지털타임스",
    # 통신사
    "yna.co.kr":"연합뉴스", "news1.kr":"뉴스1", "newsis.com":"뉴시스",
    "newspim.com":"뉴스핌",
    # 공중파·종편
    "kbs.co.kr":"KBS", "news.kbs.co.kr":"KBS", "imbc.com":"MBC", "mbc.co.kr":"MBC",
    "imnews.imbc.com":"MBC", "news.sbs.co.kr":"SBS", "sbs.co.kr":"SBS",
    "jtbc.co.kr":"JTBC", "ytn.co.kr":"YTN", "mbn.co.kr":"MBN",
    "tvchosun.com":"TV조선", "ichannela.com":"채널A",
    "yonhapnewstv.co.kr":"연합뉴스TV", "obs.co.kr":"OBS", "ebs.co.kr":"EBS",
    # 일간지 헬스
    "healthchosun.com":"헬스조선", "k-health.com":"헬스경향",
    "kukinews.com":"쿠키뉴스", "kormedi.com":"코메디닷컴",
    # 주요 의료전문지
    "doctorsnews.co.kr":"의협신문", "bosa.co.kr":"의학신문",
    "whosaeng.com":"후생신보", "dailymedi.com":"데일리메디",
    "medicaltimes.com":"메디칼타임즈", "docdocdoc.co.kr":"청년의사",
    "monews.co.kr":"메디칼업저버", "medipana.com":"메디파나뉴스",
    "medigatenews.com":"메디게이트뉴스", "rapportian.com":"라포르시안",
    "mdtoday.co.kr":"메디컬투데이", "newsmp.com":"의약뉴스",
    "hitnews.co.kr":"히트뉴스", "koreabiomed.com":"코리아바이오메드",
    "pharmstoday.com":"메디팜스투데이", "ggmedinews.com":"경기메디뉴스",
    "bokuennews.com":"보건뉴스", "medicalworldnews.co.kr":"메디컬월드뉴스",
    "healthinnews.co.kr":"헬스인뉴스", "healtho.co.kr":"헬스오",
    "yakup.com":"약업신문", "kpanews.co.kr":"약사공론", "pharmnews.com":"팜뉴스",
    "ibric.org":"브릭", "medipharmtimes.com":"메디팜타임즈",
    # 인터넷 매체
    "wowtv.co.kr":"한경TV", "dailian.co.kr":"데일리안",
    "nocutnews.co.kr":"노컷뉴스", "cbs.co.kr":"CBS",
    "sisapress.com":"시사저널", "sisaweek.com":"시사위크",
    "newstomato.com":"뉴스토마토", "zdnet.co.kr":"지디넷코리아",
    "inews24.com":"아이뉴스24", "asiatoday.co.kr":"아시아투데이",
    "betanews.net":"베타뉴스", "newsway.co.kr":"뉴스웨이",
    "greened.kr":"녹색경제신문", "veritas-a.com":"베리타스알파",
    "paxnetnews.com":"팍스넷뉴스", "100ssd.co.kr":"백세시대",
    "healthi.kr":"헬스앤라이프", "econovill.com":"이코노믹리뷰",
    "newsquest.co.kr":"뉴스퀘스트", "biztribune.co.kr":"비즈트리뷴",
    "ebn.co.kr":"EBN", "mtn.co.kr":"머니투데이방송",
    "seoulwire.com":"서울와이어", "todaykorea.co.kr":"투데이코리아",
    "news2day.co.kr":"뉴스투데이", "mydaily.co.kr":"마이데일리",
    "g-enews.com":"글로벌이코노믹", "thedailypost.kr":"데일리포스트",
    "enewstoday.co.kr":"이뉴스투데이", "megaeconomy.co.kr":"메가경제",
    "wikileaks-kr.org":"위키리크스한국", "nbntv.co.kr":"NBN",
    "lifein.co.kr":"라이프인", "widedaily.com":"와이드데일리",
    "topstarnews.com":"탑스타뉴스", "thepowernews.co.kr":"더파워뉴스",
    "thepublic.kr":"더퍼블릭", "ziks.net":"직썰",
    "iusm.co.kr":"울산의대뉴스", "dongascience.com":"동아사이언스",
    "joins.co.kr":"중앙일보", "joynews24.com":"조이뉴스24",
    "kukinews.com":"쿠키뉴스", "newdailybiz.co.kr":"뉴데일리경제",
    "cbsnews.co.kr":"CBS", "menews.co.kr":"모닝경제",
    "kben.co.kr":"기업경제신문", "emoneynews.co.kr":"이머니뉴스",
    "wiznews.co.kr":"위즈뉴스", "aitimes.com":"인공지능신문",
    "mdcreport.naver.com":"메디컬리포트뉴스",
    "kmedinfo":"e-의료정보", "doctorstimes":"의사신문",
    "sportsworldi":"스포츠월드", "gukjenews":"국제뉴스",
    "dynews":"동양일보", "thefirstmedia":"더퍼스트",
    "lawissue.co.kr":"로이슈", "cnbizm.com":"CNB저널", "jemin.com":"제민일보",
    "starnewskorea.com":"스타뉴스", "biznews.co.kr":"비즈월드",
    "viva100.com":"브릿지경제", "worktoday.co.kr":"워크투데이",
    "ccdailynews.com":"충청데일리", "hidoc.co.kr":"하이닥", "sportsq.co.kr":"스포츠Q",
}

# 네이버 뉴스 링크일 때 originallink 없는 경우 처리용 도메인 블랙리스트
NAVER_DOMAINS = {"news.naver.com", "n.news.naver.com", "m.news.naver.com"}


def clean_text(t):
    return html.unescape(re.sub(r"<.*?>", "", t)).strip()

def extract_media(orig_url, naver_url=""):
    """언론사 원문 URL 우선, 없으면 빈 문자열 (네이버 도메인은 무의미)"""
    for url in [orig_url, naver_url]:
        if not url:
            continue
        try:
            domain = re.sub(r"https?://(www\.)?", "", url).split("/")[0].lower()
            if domain in NAVER_DOMAINS:
                continue
            for key, name in MEDIA_MAP.items():
                if key in domain:
                    return name
            # 매핑 없으면 도메인 두 번째 파트 (co.kr 앞)
            parts = domain.split(".")
            if len(parts) >= 2:
                # example.co.kr → example / example.com → example
                if parts[-1] in ("kr","jp","cn","uk") and len(parts) >= 3:
                    return parts[-3]
                return parts[-2]
            return domain
        except Exception:
            continue
    return ""

def extract_professor(title, summary):
    """제목+요약에서 아산병원 교수명 추출"""
    combined = title + " " + summary
    # 패턴: 이름(2~4자) + 교수 / 교수 + 이름
    patterns = [
        r"([가-힣]{2,4})\s*(?:서울아산병원|아산병원)?\s*(?:\w+과?\s*)?교수",
        r"(?:서울아산병원|아산병원)\s*(?:\w+과?\s*)?([가-힣]{2,4})\s*교수",
        r"교수\s+([가-힣]{2,4})(?:\s|,|·|$)",
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, combined):
            name = m.group(1).strip()
            if 2 <= len(name) <= 4 and name not in found:
                found.append(name)
    return ", ".join(found[:3])  # 최대 3명

def is_excluded(title: str, summary: str = "") -> bool:
    combined = title + " " + summary
    # 키워드 제외
    if any(kw in combined for kw in EXCLUDE_KEYWORDS):
        return True
    # 패턴 제외
    if EXCLUDE_PATTERN.search(combined):
        return True
    return False

def title_key(title):
    """제목 정규화: 특수문자 모두 제거 후 앞 30자로 중복 감지"""
    t = re.sub(r"[^\w가-힣a-zA-Z0-9]", "", title)
    return t.lower()[:30]

def collect_news(query):
    collected, now = [], datetime.now(KST)
    for start in range(1, 1001, 100):
        try:
            resp = requests.get(
                "https://openapi.naver.com/v1/search/news.json",
                headers={"X-Naver-Client-Id":CLIENT_ID,"X-Naver-Client-Secret":CLIENT_SECRET},
                params={"query":query,"display":100,"start":start,"sort":"date"},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"  API 오류: {e}"); break

        items = resp.json().get("items", [])
        if not items: break

        for item in items:
            pub_dt = parsedate_to_datetime(item["pubDate"]).astimezone(KST)
            if start_dt <= pub_dt < end_dt:
                title = clean_text(item.get("title",""))
                summ  = clean_text(item.get("description",""))
                if not is_excluded(title, summ):
                    orig  = item.get("originallink","")
                    naver = item.get("link","")
                    # 제외 매체 체크
                    url_check = (orig or naver or "").lower()
                    if any(em in url_check for em in EXCLUDE_MEDIA):
                        continue
                    collected.append({
                        "날짜":     pub_dt.strftime("%Y-%m-%d"),
                        "검색어":   query,
                        "매체":     extract_media(orig, naver),
                        "제목":     title,
                        "교수명":   extract_professor(title, summ),
                        "요약":     summ,
                        "언론사원문": orig,
                        "네이버링크": naver,
                        "발행일시": pub_dt.strftime("%Y-%m-%d %H:%M"),
                        "수집일시": now.strftime("%Y-%m-%d %H:%M"),
                    })

        last_pub = parsedate_to_datetime(items[-1]["pubDate"]).astimezone(KST)
        if last_pub < start_dt: break
    return collected

def load_saved_urls():
    """이미 저장된 기사 링크. 백필로 같은 날짜를 다시 수집해도 중복으로 쌓이지 않게 한다.
    제목은 쓰지 않는다 — '[오늘의 인사]'처럼 매번 같은 제목으로 나오는 연재가 영영 빠지기 때문"""
    urls = set()
    if not CSV_PATH.exists():
        return urls
    try:
        with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                url = (row.get("언론사원문") or row.get("네이버링크") or "").strip()
                if url:
                    urls.add(url)
    except Exception as e:
        print(f"기존 기사 링크 확인 실패({e}) — 중복 검사 없이 진행")
    return urls


def collect_all():
    all_items = []
    # 이미 저장된 링크 + 이번 실행에서 본 링크·제목
    seen_urls   = load_saved_urls()
    seen_titles = set()
    print(f"기존 저장 기사 {len(seen_urls)}건은 건너뜁니다\n")

    for group, queries in HOSPITAL_QUERIES.items():
        group_count = 0
        for query in queries:
            news = collect_news(query)
            added = 0
            for item in news:
                url_key = item["언론사원문"] or item["네이버링크"]
                t_key   = title_key(item["제목"])

                if url_key and url_key in seen_urls:
                    continue
                if t_key and t_key in seen_titles:
                    continue

                if url_key: seen_urls.add(url_key)
                if t_key:   seen_titles.add(t_key)

                item["병원그룹"] = group
                all_items.append(item)
                added += 1
            group_count += added
            print(f"  [{group}] {query}: {len(news)}건 → 신규 {added}건")
        print(f"  ▶ [{group}] 합계: {group_count}건\n")
    return all_items

def save_to_csv(items):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    has_content = CSV_PATH.exists() and CSV_PATH.stat().st_size > 0
    fieldnames = FIELDNAMES
    if has_content:
        # 기존 파일의 열 순서를 따라야 이어 붙인 행이 한 칸씩 밀리지 않는다
        with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
            fieldnames = next(csv.reader(f), None) or FIELDNAMES
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", restval="")
        if not has_content:
            writer.writeheader()
        writer.writerows(items)
    print(f"CSV 저장 완료: {len(items)}건")

if __name__ == "__main__":
    print(f"수집 기간: {start_dt} ~ {end_dt}\n")
    news = collect_all()
    print(f"\n총 {len(news)}건 (중복 제거 후)")
    if news:
        save_to_csv(news)
