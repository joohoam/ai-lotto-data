import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

import requests
from bs4 import BeautifulSoup

OUT = "data/region_1to2.json"
POST_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645"
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://dhlottery.co.kr/",
}

# ✅ 최신 N회만 유지/갱신 (환경변수로 조정 가능)
RANGE = int(os.getenv("REGION_RANGE", "40"))

MAX_PAGES = int(os.getenv("REGION_MAX_PAGES", "250"))
SLEEP_PER_PAGE = float(os.getenv("REGION_SLEEP_PER_PAGE", "0.12"))
TIMEOUT = int(os.getenv("REGION_TIMEOUT", "25"))

# ✅ 파일 내 rounds 키 정렬: 최신회차가 위
SORT_DESC = os.getenv("REGION_SORT_DESC", "1") == "1"

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
    # 기존 파일의 latestRound가 있으면 그 근처에서 찾되,
    # 없으면 1200부터 탐색
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


def detect_sido(addr: str) -> Optional[str]:
    if not addr:
        return None
    a = normalize_text(addr)
    m = SIDO_RE.match(a)
    return m.group(1) if m else None


def extract_address_from_row_cells(cells: List[str]) -> Optional[str]:
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


def parse_rows_from_table(tb) -> List[List[str]]:
    if tb is None:
        return []

    rows: List[List[str]] = []
    for tr in tb.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if not tds:
            continue

        cells = [normalize_text(td.get_text(" ", strip=True)) for td in tds]
        headerish = " ".join(cells)

        if ("상호" in headerish and ("소재지" in headerish or "주소" in headerish)):
            continue
        if "조회" in headerish and "없" in headerish:
            continue

        if len(cells) >= 3:
            rows.append(cells)

    return rows


def find_store_table(soup: BeautifulSoup):
    tables = soup.find_all("table")
    best = None
    best_score = -1.0

    for tb in tables:
        txt = normalize_text(tb.get_text(" ", strip=True))
        score = 0.0
        if "상호" in txt:
            score += 2
        if "소재지" in txt or "주소" in txt:
            score += 2
        if "번호선택구분" in txt or "구분" in txt:
            score += 1
        score += min(len(tb.find_all("tr")), 30) * 0.05

        if score > best_score:
            best_score = score
            best = tb

    return best


def fetch_rank_page(session: requests.Session, round_no: int, rank_no: int, page: int) -> BeautifulSoup:
    data = {
        "method": "topStore",
        "nowPage": str(page),
        "rankNo": str(rank_no),    # ✅ 1/2 명시
        "rank": str(rank_no),
        "gameNo": "5133",
        "drwNo": str(round_no),
        "schKey": "all",
        "schVal": "",
    }

    r = session.post(POST_URL, headers=HEADERS, data=data, timeout=TIMEOUT)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding
    return BeautifulSoup(r.text, "lxml")


def fetch_rank_rows(session: requests.Session, round_no: int, rank_no: int) -> List[List[str]]:
    seen = set()
    all_rows: List[List[str]] = []

    for page in range(1, MAX_PAGES + 1):
        soup = fetch_rank_page(session, round_no, rank_no, page)
        tb = find_store_table(soup)
        rows = parse_rows_from_table(tb)

        new_cnt = 0
        for cells in rows:
            key = "|".join(cells)
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(cells)
            new_cnt += 1

        if new_cnt == 0:
            break

        time.sleep(SLEEP_PER_PAGE)

    return all_rows


def tally(rows: List[List[str]]) -> Dict[str, Any]:
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


def sort_and_trim_rounds(rounds_obj: Dict[str, Any], latest: int) -> Dict[str, Any]:
    """
    ✅ 최신 RANGE개만 남기고(삭제), 보기 좋게 정렬
    """
    start_round = max(1, latest - (RANGE - 1))
    keep = {str(r): rounds_obj.get(str(r)) for r in range(start_round, latest + 1)}
    keep = {k: v for k, v in keep.items() if v is not None}

    keys = sorted(keep.keys(), key=lambda x: int(x), reverse=SORT_DESC)
    return {k: keep[k] for k in keys}


def main():
    ensure_dirs()
    session = requests.Session()

    latest = get_latest_round_guess(session)
    start_round = max(1, latest - (RANGE - 1))

    # 기존 파일을 읽되, 마지막에 최신 RANGE만 남길 거라 오래된 건 무시해도 됨
    rounds_obj: Dict[str, Any] = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                old = json.load(f) or {}
            if isinstance(old.get("rounds"), dict):
                rounds_obj = old["rounds"]
        except Exception:
            rounds_obj = {}

    # ✅ 최신 RANGE 회를 항상 최신으로 덮어쓰기
    for rnd in range(start_round, latest + 1):
        try:
            rounds_obj[str(rnd)] = {
                "rank1": tally(fetch_rank_rows(session, rnd, 1)),
                "rank2": tally(fetch_rank_rows(session, rnd, 2)),
            }
            print(f"[region] round={rnd} updated")
        except Exception as e:
            print(f"[region] ERROR round={rnd}: {e}")
        time.sleep(0.12)

    rounds_obj = sort_and_trim_rounds(rounds_obj, latest)

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
