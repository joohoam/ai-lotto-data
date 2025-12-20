import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

OUT = "data/region_1to2.json"

# ✅ 회차별 배출점 페이지 (HTML 내에 1등/2등 섹션이 같이 있는 경우가 많음)
STORE_URL = "https://dhlottery.co.kr/store.do?method=topStore&drwNo={round}&pageGubun=L645"

# 최신 회차 확인용 API
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://dhlottery.co.kr/",
}

# ✅ 운영 파라미터(필요시 Actions env로 조정 가능)
RANGE = int(os.getenv("REGION_RANGE", "5"))  # 기본 5회만 갱신
TIMEOUT = int(os.getenv("REGION_TIMEOUT", "25"))

SIDO_LIST = [
    "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산",
    "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]
SIDO_RE = re.compile(r"^(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)\b")


def ensure_dirs():
    os.makedirs("data", exist_ok=True)


def now_kst_iso():
    kst = timezone(timedelta(hours=9))
    return datetime.now(tz=kst).isoformat(timespec="seconds")


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def fetch_json(session: requests.Session, url: str, timeout=20) -> dict:
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def is_success_round(d: dict) -> bool:
    return (d.get("returnValue") == "success") and isinstance(d.get("drwNo"), int) and d.get("drwNo", 0) > 0


def get_latest_round_guess(session: requests.Session, max_tries: int = 80) -> int:
    start = 1200
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                j = json.load(f) or {}
            start = int((j.get("meta", {}) or {}).get("latestRound", start))
        except Exception:
            pass

    cand = start
    for _ in range(max_tries):
        js = fetch_json(session, API_ROUND.format(round=cand), timeout=20)
        if is_success_round(js) and js.get("drwNo") == cand:
            js2 = fetch_json(session, API_ROUND.format(round=cand + 1), timeout=20)
            if is_success_round(js2):
                cand += 1
                time.sleep(0.12)
                continue
            return cand
        cand -= 1
        time.sleep(0.12)

    raise RuntimeError("latestRound를 찾지 못했습니다.")


def fetch_round_html(session: requests.Session, round_no: int) -> BeautifulSoup:
    url = STORE_URL.format(round=round_no)
    r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding
    return BeautifulSoup(r.text, "lxml")


def detect_sido(addr: str) -> Optional[str]:
    if not addr:
        return None
    a = normalize_text(addr)
    m = SIDO_RE.match(a)
    return m.group(1) if m else None


def extract_address_from_row_cells(cells: list[str]) -> Optional[str]:
    texts = [normalize_text(c) for c in cells if normalize_text(c)]

    for t in texts:
        if detect_sido(t):
            return t

    joined = " | ".join(texts)
    m = re.search(
        r"(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)\s+[^|]+",
        joined,
    )
    if m:
        return normalize_text(m.group(0))
    return None


def parse_rows_from_table(tb) -> list[list[str]]:
    if tb is None:
        return []

    rows: list[list[str]] = []
    for tr in tb.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if not tds:
            continue
        cells = [normalize_text(td.get_text(" ", strip=True)) for td in tds]
        headerish = " ".join(cells)

        # 헤더 제거
        if ("상호" in headerish and ("소재지" in headerish or "주소" in headerish)):
            continue
        # "조회된 내역이 없습니다" 제거
        if "조회" in headerish and "없" in headerish:
            continue

        if len(cells) >= 3:
            rows.append(cells)

    return rows


def find_rank_table(soup: BeautifulSoup, rank_no: int):
    """
    ✅ HTML 안에서 "1등/2등 ... 배출/판매점/당첨" 같은 라벨이 있는 섹션을 찾고,
    그 다음에 나오는 table을 해당 등수 테이블로 간주.
    (rankNo 파라미터가 무시되어도 이 방식은 동작)
    """
    rank = str(rank_no)
    candidates = []

    # 라벨 후보 태그들
    for tag in soup.find_all(["h2", "h3", "h4", "h5", "strong", "p", "span", "div", "li", "a"]):
        txt = normalize_text(tag.get_text(" ", strip=True))
        if not txt:
            continue

        # "1등", "2등" 포함 + 배출/판매점/당첨 같은 키워드
        if (f"{rank}등" in txt or f"{rank} 등" in txt) and ("배출" in txt or "판매점" in txt or "당첨" in txt):
            tb = tag.find_next("table")
            if tb is not None:
                candidates.append(tb)

    # 후보가 여러 개면 데이터 row가 많은 테이블을 선택
    best = None
    best_n = -1
    for tb in candidates:
        n = len(tb.select("tbody tr")) or len(tb.find_all("tr"))
        if n > best_n:
            best_n = n
            best = tb

    return best


def parse_rank_tables_fallback(soup: BeautifulSoup):
    """
    마지막 보험: 테이블을 여러 개 스캔해서 주소 히트가 나는 테이블 2개를 뽑음
    """
    tables = soup.find_all("table")
    candidate = []

    for tb in tables:
        trs = tb.find_all("tr")
        if len(trs) < 2:
            continue
        context = normalize_text(tb.get_text(" ", strip=True))
        score = 0
        if "소재지" in context or "주소" in context:
            score += 2
        if "상호" in context:
            score += 1
        candidate.append((score, tb))

    candidate.sort(key=lambda x: x[0], reverse=True)
    top = [tb for _, tb in candidate[:8]]

    parsed = []
    for tb in top:
        rows = parse_rows_from_table(tb)

        # 주소 히트가 최소 1개라도 있어야 채택
        hits = 0
        for cells in rows[:15]:
            addr = extract_address_from_row_cells(cells)
            if detect_sido(addr or ""):
                hits += 1
        if hits >= 1:
            parsed.append(rows)

    if len(parsed) >= 2:
        return parsed[0], parsed[1]
    if len(parsed) == 1:
        return parsed[0], []
    return [], []


def tally(rows: list[list[str]]):
    by_sido = {s: 0 for s in SIDO_LIST}
    internet = 0
    other = 0
    total = 0

    for cells in rows:
        joined = normalize_text(" ".join(cells))
        if not joined:
            continue

        if "인터넷" in joined or "동행복권" in joined or "dhlottery" in joined:
            internet += 1
            total += 1
            continue

        addr = extract_address_from_row_cells(cells)
        sido = detect_sido(addr or "")
        total += 1

        if sido and sido in by_sido:
            by_sido[sido] += 1
        else:
            other += 1

    return {
        "totalStores": total,
        "bySido": by_sido,
        "internet": internet,
        "other": other,
    }


def fetch_round_region(session: requests.Session, round_no: int) -> dict:
    soup = fetch_round_html(session, round_no)

    tb1 = find_rank_table(soup, 1)
    tb2 = find_rank_table(soup, 2)

    r1_rows = parse_rows_from_table(tb1) if tb1 else []
    r2_rows = parse_rows_from_table(tb2) if tb2 else []

    # fallback (라벨 기반 탐지가 실패한 경우)
    if not r1_rows or not r2_rows:
        fb1, fb2 = parse_rank_tables_fallback(soup)
        if not r1_rows:
            r1_rows = fb1
        if not r2_rows:
            r2_rows = fb2

    print(f"[region] round={round_no} r1_rows={len(r1_rows)} r2_rows={len(r2_rows)}")

    return {"rank1": tally(r1_rows), "rank2": tally(r2_rows)}


def main():
    ensure_dirs()
    session = requests.Session()

    latest = get_latest_round_guess(session)
    start_round = max(1, latest - (RANGE - 1))

    rounds_obj = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                old = json.load(f) or {}
            if isinstance(old.get("rounds"), dict):
                rounds_obj = old["rounds"]
        except Exception:
            rounds_obj = {}

    for rnd in range(start_round, latest + 1):
        try:
            rounds_obj[str(rnd)] = fetch_round_region(session, rnd)
        except Exception as e:
            print(f"[region] ERROR round={rnd}: {e}")
        time.sleep(0.15)

    out = {
        "meta": {"latestRound": latest, "range": RANGE, "updatedAt": now_kst_iso()},
        "rounds": rounds_obj,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[region] wrote {OUT}")


if __name__ == "__main__":
    main()
