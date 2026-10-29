"""AI 판정 — 수집한 기사를 Claude(경량 모델)로 분류해 docs/ai_labels.json에 쌓는다.

기사마다 묻는 것
  · 모든 병원: 논조(긍정/중립/부정), 위기 유형, 정책 관심 주제와 관련 분야
  · 서울아산병원: 실적 포함/제외와 사유, 기사에 나온 우리 의료진과 진료과,
                  어느 보도자료에서 나온 기사인지와 그 메시지가 얼마나 담겼는지
  · 타병원: 우리가 참고할 만한 점(신기술·신규 센터·국내 최초 등) 한 줄
하루 단위로 묻는 것
  · 서울아산병원 기사를 같은 사건끼리 묶은 '오늘의 이슈' 최대 5개

이미 판정한 기사는 다시 묻지 않는다 — 매일 돌면 새 기사만 처리한다.
ANTHROPIC_API_KEY가 없으면(운영 저장소) 아무것도 하지 않고 끝난다.

환경변수
  AI_DAYS       최근 며칠치를 볼지 (기본 60)
  AI_MAX_CALLS  한 번 실행에서 API를 부르는 최대 횟수 (기본 800)
  AI_MINUTES    실행 시간 한도 (기본 40분)
  AI_WORKERS    동시에 보내는 요청 수 (기본 4)
"""
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from naver_to_csv import is_excluded, title_key

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
CSV_PATH = Path("docs/news.csv")
OUT_PATH = Path("docs/ai_labels.json")
PRESS_PATH = Path("docs/press_releases.json")

MODEL = "claude-haiku-5-5"
PRICE_IN, PRICE_OUT = 0.10, 0.50   # 달러 / 100만 토큰 (100K 토큰 이하 요청 기준)

DAYS = int(os.environ.get("AI_DAYS", "60"))
MAX_CALLS = int(os.environ.get("AI_MAX_CALLS", "800"))
MINUTES = float(os.environ.get("AI_MINUTES", "40"))
WORKERS = int(os.environ.get("AI_WORKERS", "4"))

ASAN_BATCH = 15     # 보도자료 목록까지 함께 보내므로 조금 작게
OTHER_BATCH = 20
ISSUE_MAX_ARTICLES = 80

# 화면(isNoise)과 같은 기준 — 이 검색어로 들어온 기사는 무조건 제외한다
NOISE_QUERIES = {"서울중앙병원", "서울의대"}

TONES = ["긍정", "중립", "부정"]
CRISES = ["없음", "의료사고", "소송·분쟁", "개인정보·보안", "노사·인력", "환자 불만·민원", "비리·수사", "기타 부정 이슈"]
POLICY_AREAS = ["", "기획·경영", "보험·수가", "교육·수련", "진료", "연구", "간호", "대외협력·홍보", "법무·규제"]
REFLECT = ["", "높음", "보통", "낮음"]

# ── 저장 형식 (docs/ai_labels.json) ───────────────────────────────────────────
# 크기를 줄이려고 기사별 값은 짧은 키로 둔다. 화면(index.html)의 AI_KEYS와 맞춰야 한다.
#   t 논조 · c 위기 · p 정책 주제 · pa 정책 분야
#   s 실적(포함/제외) · sr 실적 사유 · d 의료진 [[이름, 진료과], …]
#   pr 보도자료 키('배포일|제목') · pm 메시지 반영(높음/보통/낮음)
#   b 타병원 참고점

COMMON_GUIDE = f"""당신은 서울아산병원 홍보팀의 언론 모니터링 담당자를 돕습니다.
기사 제목과 요약만 보고 판단하세요. 모르면 추측하지 말고 기본값을 고르세요.

[논조] 기사에서 해당 병원이 어떻게 비치는지: {"/".join(TONES)}.
  병원의 성과·신기술·수상·의료진 조언은 긍정, 단순 언급·사실 전달은 중립,
  병원이 비판받거나 사고·분쟁의 당사자면 부정.
[위기] 병원이 대응해야 할 수 있는 부정 이슈의 유형. 없으면 "없음".
  고를 수 있는 값: {", ".join(CRISES)}
[정책] 정부·국회의 보건의료 정책이나 제도(수가, 건강보험, 의대 정원, 전공의 수련,
  상급종합병원 구조전환, 비대면진료, 의료법, 필수의료 등)를 다루고 대형병원 운영에
  영향을 줄 기사면 주제를 10자 안팎으로 적으세요(예: "전공의 수련 개편"). 아니면 "".
  정책분야는 {", ".join(a for a in POLICY_AREAS if a)} 중 하나, 정책이 아니면 "".
"""

ASAN_GUIDE = COMMON_GUIDE + """
[실적] 서울아산병원 '연간 언론실적'에 넣을 기사인지: 포함/제외.
  포함: 서울아산병원이나 그 의료진이 기사의 주인공이거나, 의료진이 전문가로서
        의미 있는 설명·코멘트를 한 기사. 제약사·기업 발표라도 우리 교수의 연구 결과나
        설명이 기사의 핵심 정보면 포함.
        **우리 의료진이 직접 쓴 기고·칼럼도 포함**입니다 — 글 내용이 의학이 아니어도,
        끝에 '○○ 울산의대·서울아산병원 교수'처럼 필자로 적혀 있으면 우리 지면 실적입니다.
        '[인문산책]' 같은 연재 칼럼이 여기 해당합니다. 교수 소개만 있다고 제외하지 마세요.
        **우리 병원을 직접 찾아가 취재한 르포·현장 기사도 포함**입니다 —
        '서울아산병원 … 현장 가보니'처럼 취재 장소가 우리 병원이면, 업계 통계나
        다른 회사·병원이 함께 나와도 포함입니다.
        **다른 기관 사람과 함께 받은 상이라도 우리 의료진이 수상자면 포함**입니다 —
        제목이 다른 대학·병원 사람 이름이어도, 본문에 '○○ 교수(서울아산병원 종양내과)'처럼
        우리 사람이 수상자로 적혀 있으면 우리 실적입니다. 소속을 밝힌 것을 '경력 소개'로 보지 마세요.
        **병원의 모금·후원금 운영을 취재한 기획·분석 기사도 포함**입니다 —
        우리 병원의 모금액·기부자 예우 제도를 본문에서 다뤘으면, 다른 병원이 함께 나와도 포함입니다.
        **기업이 주어인 기사라도 우리 병원·의료진이 그 일의 수행 주체면 포함**입니다 —
        우리 교수가 창업한 회사, 우리 병원의 연구 역량·의료진이 사업의 알맹이인 경우,
        협약을 맺고 과제를 함께 수행하는 경우가 여기 해당합니다.
        '기업 기사'라는 이유만으로 제외하지 마세요.
  제외: 병원 이름이 지나가듯 나오는 기사, 다른 아산병원(강릉·정읍 등)이나 충남 아산시,
        의료진의 출신·경력 소개, 기업 IR·주가·계약·수주, 공동연구 참여기관으로 이름만
        나열, 연예인·인물 화제, 기부·후원 전달식(주고받는 자리를 전하는 단신에 한정), 원내 약 처방
        등재, 부동산·분양 광고, 사건·노동 이슈, 지자체·타기관 행사.
        '여러 병원을 나열한 기사'는 우리 병원이 **목록의 한 줄로만** 나올 때입니다.
        우리 병원이 취재 현장이거나 주된 사례면 다른 병원이 함께 나와도 포함입니다.
        기업 기사의 제외 기준은 **우리 병원이 그 사업의 '제휴처·배경'으로만** 나오는 것입니다 —
        아파트 입주민 서비스의 연계 검진센터, 부동산·건설·분양, 제품을 납품받는 곳으로
        이름만 나온 경우, 다른 사람 이야기 속 지나가는 언급, 관련기사 목록에만 나오는 경우.
  실적사유는 판단 근거를 20자 안팎으로.
[의료진] 기사에 이름이 나온 서울아산병원 소속 의사·간호사·연구자를 [이름, 진료과]로.
  진료과(예: 소화기내과, 심장내과, 소아외과)가 없으면 "". 다른 병원 사람은 넣지 마세요.
[보도자료] 아래 보도자료 목록 중 이 기사가 받아쓴 것이 있으면 그 번호, 없으면 0.
  같은 주제를 다룬 것만으로는 부족합니다 — 같은 연구·행사·인물·사건이어야 합니다.
  반영은 보도자료 제목이 내세운 핵심 메시지(성과, 최초, 환자에게 주는 의미)가 기사에
  얼마나 담겼는지: 높음/보통/낮음. 보도자료가 0이면 "".
"""

OTHER_GUIDE = COMMON_GUIDE + """
[참고점] 서울아산병원이 벤치마킹하거나 눈여겨볼 점 — 신기술·신장비 도입, 신규 센터 개소,
  '국내 최초'·'세계 최초', 대규모 투자, 새 진료 체계, 제도 선도 사례 등이면 30자 안팎 한 줄.
  단순 질환 정보, 인사, 기부, 일반 동정이면 "".
  건강강좌·공개강좌·설명회·행사 안내, 진료 안내, 휴진 공지처럼 벤치마킹할 내용이 없는
  단순 공지도 ""로 두세요.
"""

ISSUE_GUIDE = """당신은 서울아산병원 홍보팀의 언론 모니터링 담당자를 돕습니다.
하루 동안 나온 서울아산병원 관련 기사 목록을 같은 사건·주제끼리 묶어
중요한 이슈를 최대 5개 고르세요. 기사가 여러 매체에 나온 이슈, 병원 평판에 영향이 큰
이슈를 먼저 고르세요. 제목은 20자 안팎, 요약은 두 문장 이내로 쓰세요.
기사는 번호로 가리키고, 목록에 없는 번호는 쓰지 마세요."""


def _str(enum=None):
    return {"type": "string", "enum": enum} if enum else {"type": "string"}


def _schema(item_props):
    item = {"type": "object", "properties": item_props,
            "required": list(item_props), "additionalProperties": False}
    return {"type": "object", "properties": {"결과": {"type": "array", "items": item}},
            "required": ["결과"], "additionalProperties": False}


COMMON_PROPS = {
    "번호": {"type": "integer"},
    "논조": _str(TONES),
    "위기": _str(CRISES),
    "정책주제": _str(),
    "정책분야": _str(POLICY_AREAS),
}
ASAN_SCHEMA = _schema({
    **COMMON_PROPS,
    "실적": _str(["포함", "제외"]),
    "실적사유": _str(),
    "의료진": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
    "보도자료": {"type": "integer"},
    "반영": _str(REFLECT),
})
OTHER_SCHEMA = _schema({**COMMON_PROPS, "참고점": _str()})
ISSUE_SCHEMA = {
    "type": "object",
    "properties": {"이슈": {"type": "array", "items": {
        "type": "object",
        "properties": {"제목": _str(), "요약": _str(),
                       "기사": {"type": "array", "items": {"type": "integer"}}},
        "required": ["제목", "요약", "기사"], "additionalProperties": False}}},
    "required": ["이슈"], "additionalProperties": False,
}


# ── 데이터 ───────────────────────────────────────────────────────────────────
def link_of(row):
    return row.get("언론사원문") or row.get("네이버링크") or ""


def read_articles():
    """화면과 같은 기준으로 중복·노이즈를 걸러 최근 DAYS일치만 남긴다"""
    since = (NOW.date() - timedelta(days=DAYS)).isoformat()
    seen_urls, seen_titles, out = set(), set(), []
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("제목"):
                continue
            url, tk = link_of(row), title_key(row["제목"])
            if (url and url in seen_urls) or (tk and tk in seen_titles):
                continue
            if url:
                seen_urls.add(url)
            if tk:
                seen_titles.add(tk)
            day = (row.get("날짜") or "")[:10]
            if not url or day < since:
                continue
            if row.get("검색어") in NOISE_QUERIES or is_excluded(row["제목"], row.get("요약") or ""):
                continue
            out.append(row)
    return out


def load_press():
    try:
        items = json.loads(PRESS_PATH.read_text(encoding="utf-8")).get("목록") or []
    except (OSError, ValueError):
        return []
    # 담당자 이름은 보내지도, 저장하지도 않는다
    return [{"날짜": it.get("날짜", ""), "제목": it.get("제목", ""), "의료진": it.get("의료진") or []}
            for it in items if it.get("날짜") and it.get("제목")]


def press_key(item):
    return f'{item["날짜"]}|{item["제목"]}'


def press_window(press, day):
    """기사 날짜 기준으로 배포일이 7일 전 ~ 1일 뒤인 보도자료 (화면의 PRESS_BEFORE/AFTER와 같은 범위)"""
    d = datetime.strptime(day, "%Y-%m-%d").date()
    lo, hi = (d - timedelta(days=7)).isoformat(), (d + timedelta(days=1)).isoformat()
    return [p for p in press if lo <= p["날짜"] <= hi]


def load_labels():
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("기사", {})
    data.setdefault("이슈", {})
    return data


def save_labels(data):
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# ── API 호출 ─────────────────────────────────────────────────────────────────
class Runner:
    def __init__(self):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=4)
        self.lock = threading.Lock()
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.errors = 0
        self.fatal = None   # 키·권한 문제처럼 다시 시도해도 똑같이 막히는 오류
        self.deadline = time.monotonic() + MINUTES * 60

    def can_call(self):
        with self.lock:
            if self.fatal or self.calls >= MAX_CALLS or time.monotonic() > self.deadline:
                return False
            self.calls += 1
            return True

    def ask(self, system, user, schema, max_tokens=8000):
        """JSON 하나를 받아 온다. 실패하면 None — 다음 실행에서 같은 기사를 다시 묻는다"""
        try:
            resp = self.client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": user}],
            )
        except self.anthropic.APIError as e:
            # 키가 틀렸거나 권한이 없으면 남은 호출을 다 시도해 봐야 똑같이 막힌다.
            # 한 번만 알리고 바로 멈춘다 — 잘못된 키로 611번을 헛돌린 적이 있다
            fatal = isinstance(e, (self.anthropic.AuthenticationError,
                                   self.anthropic.PermissionDeniedError))
            with self.lock:
                self.errors += 1
                first = fatal and not self.fatal
                if first:
                    self.fatal = f"{type(e).__name__}: {str(e)[:200]}"
            if fatal:
                if first:
                    print(f"  [중단] {self.fatal}")
                    print("  인증·권한 문제라 남은 호출을 모두 건너뜁니다. "
                          "ANTHROPIC_API_KEY를 확인하세요.")
                return None
            print(f"  API 오류: {type(e).__name__}: {str(e)[:200]}")
            return None
        with self.lock:
            self.tokens_in += resp.usage.input_tokens
            self.tokens_out += resp.usage.output_tokens
        if resp.stop_reason in ("refusal", "max_tokens"):
            with self.lock:
                self.errors += 1
            print(f"  응답 중단: {resp.stop_reason}")
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except ValueError:
            with self.lock:
                self.errors += 1
            print("  JSON 해석 실패")
            return None

    def cost(self):
        return (self.tokens_in * PRICE_IN + self.tokens_out * PRICE_OUT) / 1_000_000


def article_line(i, row):
    summary = (row.get("요약") or "").replace("\n", " ")[:300]
    return f'[{i}] ({row.get("매체", "")}) {row["제목"]}\n    {summary}'


def classify_batch(runner, rows, is_asan, press):
    lines = [article_line(i + 1, r) for i, r in enumerate(rows)]
    if is_asan:
        if press:
            plist = "\n".join(f'{i + 1}. ({p["날짜"]}) {p["제목"]}'
                              + (f' — 의료진: {", ".join(p["의료진"])}' if p["의료진"] else "")
                              for i, p in enumerate(press))
        else:
            plist = "(이 기간에 배포한 보도자료 없음 — 모두 0)"
        user = f"보도자료 목록:\n{plist}\n\n기사 목록:\n" + "\n".join(lines)
        out = runner.ask(ASAN_GUIDE, user, ASAN_SCHEMA)
    else:
        user = "기사 목록 (병원: " + ", ".join(sorted({r["병원그룹"] for r in rows})) + "):\n" + "\n".join(lines)
        out = runner.ask(OTHER_GUIDE, user, OTHER_SCHEMA)
    if not out:
        return {}

    labels = {}
    for res in out.get("결과", []):
        n = res.get("번호", 0)
        if not 1 <= n <= len(rows):
            continue
        lab = {"t": res["논조"], "c": res["위기"]}
        if res.get("정책주제"):
            lab["p"] = res["정책주제"].strip()
            lab["pa"] = res.get("정책분야", "")
        if is_asan:
            lab["s"] = res["실적"]
            lab["sr"] = res.get("실적사유", "").strip()
            docs = [[str(x[0]).strip(), str(x[1]).strip() if len(x) > 1 else ""]
                    for x in res.get("의료진", []) if x and str(x[0]).strip()]
            if docs:
                lab["d"] = docs
            k = res.get("보도자료", 0)
            if press and 1 <= k <= len(press):
                lab["pr"] = press_key(press[k - 1])
                lab["pm"] = res.get("반영") or "보통"
        elif res.get("참고점"):
            lab["b"] = res["참고점"].strip()
        labels[link_of(rows[n - 1])] = lab
    return labels


def summarize_day(runner, day, rows):
    rows = rows[:ISSUE_MAX_ARTICLES]
    user = f"{day} 서울아산병원 관련 기사 {len(rows)}건:\n" + "\n".join(
        f'[{i + 1}] ({r.get("매체", "")}) {r["제목"]}' for i, r in enumerate(rows))
    out = runner.ask(ISSUE_GUIDE, user, ISSUE_SCHEMA, max_tokens=4000)
    if not out:
        return None
    issues = []
    for it in out.get("이슈", [])[:5]:
        links = [link_of(rows[n - 1]) for n in it.get("기사", []) if 1 <= n <= len(rows)]
        if links:
            issues.append({"제목": it["제목"].strip(), "요약": it["요약"].strip(), "기사": links})
    return {"기사수": len(rows), "이슈": issues}


def chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY 없음 — AI 판정을 건너뜁니다")
        return 0

    articles = read_articles()
    press = load_press()
    data = load_labels()
    done = data["기사"]
    todo = [r for r in articles if link_of(r) not in done]
    print(f"대상 {len(articles)}건 (최근 {DAYS}일) · 이미 판정 {len(articles) - len(todo)}건 · 새로 판정 {len(todo)}건")

    # 같은 날 기사끼리 묶어야 보도자료 후보 목록을 한 번만 보내면 된다
    jobs = []
    by_day = {}
    for r in todo:
        by_day.setdefault((r["날짜"][:10], r["병원그룹"] == "아산"), []).append(r)
    for (day, is_asan), rows in sorted(by_day.items(), reverse=True):   # 최근 날짜부터
        win = press_window(press, day) if is_asan else []
        for part in chunks(rows, ASAN_BATCH if is_asan else OTHER_BATCH):
            jobs.append(("기사", part, is_asan, win))

    # 오늘의 이슈 — 그날 아산 기사 수가 바뀌었으면(늦게 수집된 기사) 다시 만든다
    asan_by_day = {}
    for r in articles:
        if r["병원그룹"] == "아산":
            asan_by_day.setdefault(r["날짜"][:10], []).append(r)
    for day, rows in sorted(asan_by_day.items(), reverse=True):
        prev = data["이슈"].get(day)
        if not prev or prev.get("기사수") != min(len(rows), ISSUE_MAX_ARTICLES):
            jobs.append(("이슈", rows, day, None))

    runner = Runner()
    finished = 0

    def run(job):
        if not runner.can_call():
            return job, None
        if job[0] == "기사":
            return job, classify_batch(runner, job[1], job[2], job[3])
        return job, summarize_day(runner, job[2], job[1])

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for fut in as_completed([pool.submit(run, j) for j in jobs]):
            job, result = fut.result()
            if not result:
                continue
            with runner.lock:
                if job[0] == "기사":
                    done.update(result)
                else:
                    data["이슈"][job[2]] = result
                finished += 1
                if finished % 20 == 0:
                    save_labels(data)
                    print(f"  진행 {finished}/{len(jobs)} · 약 ${runner.cost():.3f}")

    data["실행"] = {
        "시각": NOW.strftime("%Y-%m-%d %H:%M"), "모델": MODEL,
        "호출": runner.calls, "오류": runner.errors, "작업": len(jobs), "완료": finished,
        "입력토큰": runner.tokens_in, "출력토큰": runner.tokens_out,
        "추정비용달러": round(runner.cost(), 4),
    }
    if runner.fatal:
        data["실행"]["중단"] = runner.fatal
    save_labels(data)
    print(json.dumps(data["실행"], ensure_ascii=False))
    if runner.fatal:
        # 조용히 성공으로 끝나면 빈 결과를 한참 뒤에야 알아챈다
        print(f"인증·권한 문제로 중단했습니다: {runner.fatal}", file=sys.stderr)
        return 1
    if finished < len(jobs):
        print(f"남은 작업 {len(jobs) - finished}개 — 다음 실행에서 이어서 처리합니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
