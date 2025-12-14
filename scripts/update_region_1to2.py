import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

OUT = "data/region_1to2.json"

# 로또6/45 배출점 페이지
STORE_URL = "https://dhlottery.co.kr/store.do?method=topStore&drwNo={round}&pageGubun=L645"

# 최신 회차 추정용 공식 API
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

RANGE = 40

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


def fetch_json(url: str, timeout=20) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_html(url: str, timeout=25) -> tuple[str, int]:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding
    return r.text, r.status_code


def is_success_round(d: dict) -> bool:
    return (d.get("returnValue") == "success") and isinstance(d.get("drwNo"), int) and d.get("drwNo", 0) > 0


def get_latest_round_guess(max_tries: int = 60) -> int:
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
        js = fetch_json(API_ROUND.format(round=cand))
        if is_success_round(js) and js.get("drwNo") == cand:
            js2 = fetch_json(API_ROUND.format(round=cand + 1))
            if is_success_round(js2):
                cand += 1
                time.sleep(0.12)
                continue
            return cand
        cand -= 1
        time.sleep(0.12)

    raise RuntimeError("latestRound를 찾지 못했습니다. start 값을 조정해 주세요.")


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def detect_sido(addr: str):
    if not addr:
        return None
    a = normalize_text(addr)
    m = SIDO_RE.match(a)
    return m.group(1) if m else None


def extract_address_from_row_cells(cells: list[str]) -> str | None:
    texts = [normalize_text(c) for c in cells if normalize_text(c)]
    # 1) 셀 단위에서 '시/도'로 시작하는 주소 후보
    for t in texts:
        if detect_sido(t):
            return t

    # 2) row 전체를 합쳐서 주소 패턴을 잡아보기
    joined = " | ".join(texts)
    m = re.search(
        r"(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)\s+[^|]+",
        joined,
    )
    if m:
        return normalize_text(m.group(0))

    return None


def parse_rank_tables(soup: BeautifulSoup):
    """
    1등/2등 배출점 테이블을 최대한 안정적으로 찾기:
    - 테이블을 여러 개 후보로 잡아보고
    - 실제 row에서 주소(시/도) 히트가 나오는 테이블을 채택
    """
    tables = soup.find_all("table")
    candidate = []

    for tb in tables:
        trs = tb.find_all("tr")
        if len(trs) < 2:
            continue
        context = normalize_text(tb.get_text(" ", strip=True))
        score = 0
        if "배출" in context:
            score += 1
        if "소재지" in context or "주소" in context:
            score += 2
        if "1등" in context:
            score += 1
        if "2등" in context:
            score += 1
        candidate.append((score, tb))

    candidate.sort(key=lambda x: x[0], reverse=True)
    top = [tb for _, tb in candidate[:6]]

    parsed = []
    for tb in top:
        rows = []
        for tr in tb.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if not tds:
                continue
            cells = [normalize_text(td.get_text(" ", strip=True)) for td in tds]
            headerish = " ".join(cells)
            if ("상호" in headerish and ("소재지" in headerish or "주소" in headerish)):
                continue
            rows.append(cells)

        # 주소 히트가 최소 1개라도 있어야 채택
        hits = 0
        for cells in rows[:12]:
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
        # 빈 row는 스킵
        joined = normalize_text(" ".join(cells))
        if not joined:
            continue

        # 인터넷/동행복권(온라인) 감지
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


def fetch_round_region(round_no: int) -> dict:
    url = STORE_URL.format(round=round_no)
    html, status = fetch_html(url, timeout=25)
    soup = BeautifulSoup(html, "lxml")

    r1_rows, r2_rows = parse_rank_tables(soup)

    # 로그(필요하면 남겨두세요)
    print(f"[region] round={round_no} status={status} len={len(html)} r1_rows={len(r1_rows)} r2_rows={len(r2_rows)}")

    rank1 = tally(r1_rows)
    rank2 = tally(r2_rows)

    return {"rank1": rank1, "rank2": rank2}


def main():
    ensure_dirs()

    latest = get_latest_round_guess()
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

    # 최근 RANGE 회만 갱신 (그 외는 유지)
    for rnd in range(start_round, latest + 1):
        try:
            rounds_obj[str(rnd)] = fetch_round_region(rnd)
        except Exception as e:
            print(f"[region] ERROR round={rnd}: {e}")
        time.sleep(0.35)

    out = {
        "meta": {
            "latestRound": latest,
            "range": RANGE,
            "updatedAt": now_kst_iso(),
        },
        "rounds": rounds_obj,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[region] wrote {OUT}")


if __name__ == "__main__":
    main()
