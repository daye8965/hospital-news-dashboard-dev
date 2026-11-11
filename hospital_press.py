# -*- coding: utf-8 -*-
"""병원 홈페이지에 올라온 보도자료 목록을 받아 docs/hospital_press.json에 둔다.

AI 분석 탭의 '보도자료로 나온 기사 (추정)'는 기사 모양만 보고 묶은 추정이다.
병원이 실제로 올린 보도자료와 맞춰 보면 그 묶음이 진짜 보도자료인지 확인할 수 있다.

받는 곳 (모두 공개 게시판, 날짜와 제목만 받는다):
  아산       https://news.amc.seoul.kr/news/con/list.do?pressChk=Y
  서울대     https://www.snuh.org/board/B003/list.do
  세브란스   https://sev.severance.healthcare/sev/news/press/report.do
  성모       https://www.cmcseoul.or.kr/page/board/news

삼성서울병원은 공개 보도자료 게시판을 찾지 못했다 — 홈페이지 메뉴·사이트맵에 없다.
그래서 삼성 묶음은 예전처럼 추정으로만 남는다.

가끔 손으로 돌린다. 기본은 2026-06-01까지(기사 수집 시작일 2026-06-11보다 앞) 거슬러 받는다.
    python hospital_press.py
    PRESS_FROM=2026-01-01 python hospital_press.py
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "hospital_press.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) hospital-news-dashboard"
FROM = os.environ.get("PRESS_FROM", "2026-06-01")
MAX_PAGES = 60     # 안전장치 — 날짜가 FROM 아래로 내려가면 그 전에 멈춘다
PAUSE = 0.4


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", "replace")


def clean(t):
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", t).strip()


# ── 아산: <ul class="contList"> 의 <li>, 날짜는 2026.11.09, 제목은 <strong> ──────
def parse_asan(html):
    out = []
    for li in re.findall(r"<li>(.*?)</li>", html, re.S):
        d = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", li)
        t = re.search(r"<strong>(.*?)</strong>", li, re.S)
        if d and t:
            out.append(("%s-%s-%s" % d.groups(), clean(t.group(1))))
    return out


# ── 서울대: <td class="board__tit"><a>제목</a></td> 뒤의 <td>에 2026-11-06 ──────
def parse_snuh(html):
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        t = re.search(r'<td class="board__tit">\s*<a[^>]*>(.*?)</a>', tr, re.S)
        d = re.search(r"(\d{4}-\d{2}-\d{2})", tr)
        if d and t:
            out.append((d.group(1), clean(t.group(1))))
    return out


# ── 세브란스: <div class="bbs-item"> 안의 .subject 와 .date ─────────────────────
def parse_sev(html):
    out = []
    for item in re.findall(r'<div class="bbs-item.*?</a>', html, re.S):
        t = re.search(r'<strong class="subject">(.*?)</strong>', item, re.S)
        d = re.search(r'<span class="date">(\d{4}-\d{2}-\d{2})</span>', item)
        if d and t:
            out.append((d.group(1), clean(t.group(1))))
    return out


# ── 성모: 게시판이 JSON API다. 화면은 Vue가 그려서 HTML에는 목록이 아예 없다 ──────
#    /api/article/41?p=쪽&s=개수&q={}
#    p·s·q라는 이름은 cmc.vue.js의 페이지 컴포넌트가 쓰는 것이고, 41은 보도자료
#    게시판 번호다(페이지의 <board-list board-no="41" board-id="news">).
#    총 건수는 응답 헤더 X-Cmc-Total-Count에 온다(2026-11-11 기준 1,906건).
#
#    날짜는 createdDt(epoch 밀리초)뿐이다. **KST로 읽어야 한다** — 돌리는 컴퓨터의
#    시간대로 읽으면 밤늦게 올린 보도자료의 날짜가 하루 밀린다.
#    응답에 기사 본문(content)까지 들어 있어 한 쪽이 1MB를 넘는다. 빼는 인자가 없어
#    그대로 받고 날짜·제목만 쓴다.
KST = timezone(timedelta(hours=9))


def parse_cmc(text):
    out = []
    for a in json.loads(text):
        ms, title = a.get("createdDt"), clean(a.get("title") or "")
        if not ms or not title:
            continue
        out.append((datetime.fromtimestamp(ms / 1000, KST).strftime("%Y-%m-%d"), title))
    return out


SITES = [
    {"group": "아산", "parse": parse_asan,
     "home": "https://news.amc.seoul.kr/news/con/list.do?pressChk=Y",
     "page": lambda i: "https://news.amc.seoul.kr/news/con/list.do?pressChk=Y&pageIndex=%d" % (i + 1)},
    {"group": "서울대", "parse": parse_snuh,
     "home": "https://www.snuh.org/board/B003/list.do",
     "page": lambda i: "https://www.snuh.org/board/B003/list.do?pageIndex=%d" % (i + 1)},
    {"group": "세브란스", "parse": parse_sev,
     "home": "https://sev.severance.healthcare/sev/news/press/report.do",
     "page": lambda i: ("https://sev.severance.healthcare/sev/news/press/report.do"
                        "?article.offset=%d&articleLimit=12" % (i * 12))},
    {"group": "성모", "parse": parse_cmc,
     "home": "https://www.cmcseoul.or.kr/page/board/news",
     "page": lambda i: ("https://www.cmcseoul.or.kr/api/article/41"
                        "?p=%d&s=100&q=%%7B%%7D" % (i + 1))},
]


def crawl(site):
    seen, rows = set(), []
    for i in range(MAX_PAGES):
        try:
            got = site["parse"](fetch(site["page"](i)))
        except Exception as e:                      # noqa: BLE001
            print("  ! %s %d쪽 %s" % (site["group"], i + 1, e))
            break
        if not got:
            break
        fresh = 0
        for ymd, title in got:
            key = (ymd, title)
            if not title or key in seen:
                continue
            seen.add(key)
            rows.append([ymd, title])
            fresh += 1
        oldest = min(d for d, _ in got)
        print("  %s %d쪽 %d건 (새 %d, 가장 오래된 %s)" % (site["group"], i + 1, len(got), fresh, oldest))
        if oldest < FROM:
            break
        if fresh == 0:        # 같은 쪽을 계속 받고 있다 — 페이지 인자가 안 먹는 것
            break
        time.sleep(PAUSE)
    rows = [r for r in rows if r[0] >= FROM]
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return rows


def main():
    data, src = {}, {}
    for site in SITES:
        print("%s …" % site["group"])
        rows = crawl(site)
        if not rows:
            sys.exit("%s 보도자료를 한 건도 못 읽었습니다 — 게시판 구조가 바뀌었는지 보세요."
                     % site["group"])
        data[site["group"]] = rows
        src[site["group"]] = site["home"]
        print("  → %d건 (%s ~ %s)" % (len(rows), rows[-1][0], rows[0][0]))

    out = {
        "기준일": datetime.now().strftime("%Y-%m-%d"),
        "시작일": FROM,
        "출처": src,
        "없는곳": {"삼성": "공개 보도자료 게시판을 찾지 못했습니다"},
        "보도자료": data,
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
        f.write("\n")
    print("→ %s" % OUT)


if __name__ == "__main__":
    main()
