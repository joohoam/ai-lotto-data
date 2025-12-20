import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple

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

# =========================
# 운영 파라미터 (env)
# =========================
# 파일에 유지할 최신 회차 개수(윈도우)
WINDOW = int(os.getenv("REGION_WINDOW", "40"))

# 매 실행 시 "무조건" 다시 갱신할 최신 회차 개수(당일 업데이트 흔들림 방지)
REFRESH = int(os.getenv("REGION_REFRESH", "2"))

# 초기 파일이 비어 있을 때: WINDOW 회차를 한 번에 구축할지
BOOTSTRAP_ON_EMPTY = os.getenv("REGION_BOOTSTRAP_ON_EMPTY", "1") == "1"

# 스크래핑 튜닝
MAX_PAGES = int(os.getenv("REGION_MAX_PAGES", "220"))
SLEEP_PER_PAGE = float(os.getenv("REGION_SLEEP_PER_PAGE", "0.12"))
TIMEOUT = int(os.getenv("REGION_TIMEOUT", "25"))

# 파일 표시: 최신회차가 위로 오도록 정렬
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
    # 기존 파일의 latestRound 근처에서 탐색하면 더 빠름
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
                time.sleep(0.10)
                continue
            return cand
        cand -= 1
        time.sleep(0.10)

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
        "rankNo": str(rank_no),   # ✅ 1/2 명시
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


def fetch_round_region(session: requests.Session, round_no: int) -> Dict[str, Any]:
    r1_rows = fetch_rank_rows(session, round_no, 1)
    r2_rows = fetch_rank_rows(session, round_no, 2)

    print(f"[region] round={round_no} r1_rows={len(r1_rows)} r2_rows={len(r2_rows)}")

    return {"rank1": tally(r1_rows), "rank2": tally(r2_rows)}


def load_existing() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not os.path.exists(OUT):
        return {}, {}
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        meta = d.get("meta", {}) if isinstance(d.get("meta"), dict) else {}
        rounds = d.get("rounds", {}) if isinstance(d.get("rounds"), dict) else {}
        return meta, rounds
    except Exception:
        return {}, {}


def sort_rounds(rounds_obj: Dict[str, Any]) -> Dict[str, Any]:
    keys = sorted(rounds_obj.keys(), key=lambda x: int(x), reverse=SORT_DESC)
    return {k: rounds_obj[k] for k in keys}


def trim_window(rounds_obj: Dict[str, Any], latest: int) -> Dict[str, Any]:
    start = max(1, latest - (WINDOW - 1))
    keep = {}
    for r in range(start, latest + 1):
        k = str(r)
        if k in rounds_obj:
            keep[k] = rounds_obj[k]
    return sort_rounds(keep)


def compute_targets(latest: int, rounds_obj: Dict[str, Any]) -> List[int]:
    start = max(1, latest - (WINDOW - 1))
    existing = {int(k) for k in rounds_obj.keys() if str(k).isdigit()}
    targets = set()

    if not existing:
        if BOOTSTRAP_ON_EMPTY:
            targets.update(range(start, latest + 1))
        else:
            targets.add(latest)
        return sorted(targets)

    # 최신 WINDOW 범위에서 누락된 회차 채우기
    for r in range(start, latest + 1):
        if r not in existing:
            targets.add(r)

    # 최신 REFRESH 회차는 항상 재수집
    refresh_start = max(start, latest - (max(REFRESH, 1) - 1))
    for r in range(refresh_start, latest + 1):
        targets.add(r)

    return sorted(targets)


def main():
    ensure_dirs()
    session = requests.Session()

    latest = get_latest_round_guess(session)
    _, rounds_obj = load_existing()

    targets = compute_targets(latest, rounds_obj)

    print(f"[region] latest={latest}, WINDOW={WINDOW}, REFRESH={REFRESH}, targets={targets[:6]}... total={len(targets)}")

    for rnd in targets:
        try:
            rounds_obj[str(rnd)] = fetch_round_region(session, rnd)
        except Exception as e:
            print(f"[region] ERROR round={rnd}: {e}")
        time.sleep(0.12)

    rounds_obj = trim_window(rounds_obj, latest)

    out = {
        "meta": {
            "latestRound": latest,
            "window": WINDOW,
            "refresh": REFRESH,
            "updatedAt": now_kst_iso(),
        },
        "rounds": rounds_obj,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[region] wrote {OUT}")


if __name__ == "__main__":
    main()
