# -*- coding: utf-8 -*-
"""서울아산병원 홈페이지(amc.seoul.kr)에서 의료진 이름 → 진료과 목록을 내려받는다.

보고용 엑셀의 '진료과' 칸을 손으로 채우던 것을 없애려고 만들었다.
실적 양식에 적는 진료과 이름은 병원 홈페이지 표기가 기준이라 거기서 그대로 가져온다.

흐름: 진료과 목록(deptListTypeA.do) → 진료과별 의료진 목록(staffBaseInfoList.do) →
docs/amc_doctors.json. 홈페이지에 공개된 이름·진료과만 담는다(사진·이메일·예약 정보는 안 받는다).

가끔 한 번만 돌리면 된다 — 의료진이 바뀌면 다시 돌려 JSON을 갱신한다.
    python amc_doctors.py
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

BASE = "https://www.amc.seoul.kr"
DEPT_LIST = BASE + "/asan/departments/deptListTypeA.do"
STAFF_LIST = BASE + "/asan/staff/base/staffBaseInfoList.do?drEmpId=&deptTabIndex=&searchHpCd={}&searchKeyword="
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "amc_doctors.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) hospital-news-dashboard"
PAUSE = 0.3   # 홈페이지에 부담 주지 않으려는 간격


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", "replace")


def dept_codes():
    html = fetch(DEPT_LIST)
    codes = re.findall(r"/asan/depts/([A-Z0-9]+)/K/deptLink\.do", html)
    seen, out = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def parse_staff(html):
    """(이름, 진료과 원문) 목록. 진료과는 '류마티스내과, 척추센터'처럼 여럿일 수 있다."""
    names = re.findall(r'<p class="doctor_name">\s*<a[^>]*>([^<]+)</a>', html)
    blocks = re.findall(r'<p class="doctor_name">.*?</p>(.*?)</table>', html, re.S)
    out = []
    for name, block in zip(names, blocks):
        m = re.search(r"<th[^>]*>\s*진료과\s*</th>\s*<td[^>]*>(.*?)</td>", block, re.S)
        dept = re.sub(r"<[^>]+>", "", m.group(1)) if m else ""
        dept = re.sub(r"\s+", " ", dept).strip()
        out.append((name.strip(), dept))
    return out


def main():
    codes = dept_codes()
    if not codes:
        sys.exit("진료과 목록을 읽지 못했습니다 — 홈페이지 구조가 바뀌었는지 보세요.")
    print("진료과 %d곳" % len(codes))

    people = {}    # 이름 → 대표 진료과 집합 (동명이인이면 둘 이상)
    depts = set()
    fail = 0
    for i, code in enumerate(codes, 1):
        try:
            rows = parse_staff(fetch(STAFF_LIST.format(code)))
        except Exception as e:                      # noqa: BLE001
            fail += 1
            print("  ! %s %s" % (code, e))
            continue
        for name, dept in rows:
            if not name or not dept:
                continue
            # 대표 진료과 = 맨 앞 하나. 뒤는 겸임 센터라 실적 양식에 적지 않는다
            head = dept.split(",")[0].strip()
            if not head:
                continue
            people.setdefault(name, set()).add(head)
            depts.add(head)
        if i % 20 == 0:
            print("  %d/%d  의료진 %d명" % (i, len(codes), len(people)))
        time.sleep(PAUSE)

    if fail > len(codes) // 4:
        sys.exit("진료과 %d곳 중 %d곳을 못 읽었습니다 — 저장하지 않습니다." % (len(codes), fail))

    data = {
        "기준일": datetime.now().strftime("%Y-%m-%d"),
        "출처": "https://www.amc.seoul.kr/asan/staff/base/staffBaseInfoList.do",
        "진료과": sorted(depts),
        "의료진": {n: sorted(d) for n, d in sorted(people.items())},
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=0, sort_keys=False)
        f.write("\n")
    dup = [n for n, d in data["의료진"].items() if len(d) > 1]
    print("의료진 %d명 · 진료과 %d개 · 동명이인 %d명 → %s"
          % (len(data["의료진"]), len(data["진료과"]), len(dup), OUT))
    if dup:
        print("동명이인: " + ", ".join(dup[:20]))


if __name__ == "__main__":
    main()
