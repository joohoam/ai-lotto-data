import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

OUT = "data/region_1to2.json"

# ✅ topStore는 "페이지 URL + POST 바디" 조합이 표준적으로 잘 동작함
POST_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645"
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://dhlottery.co.kr/",
}

# 운영 파라미터(필요 시 Actions env로 조절)
RANGE = int(os.getenv("REGION_RANGE", "5"))
MAX_PAGES = int(os.getenv("REGION_MAX_PAGES", "200"))   # 2등 많을 때 대비
SLEEP_PER_PAGE = float(os.getenv("REGION_SLEEP_PER_PAGE", "0.12"))
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


def find_store_table(soup: BeautifulSoup):
    """
    rankNo/nowPage로 호출한 응답에는 보통 '당첨 판매점' 테이블 1개가 중심.
    헤더에 '상호' + '소재지/주소'가 있는 테이블을 우선 선택.
    """
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

        # 헤더/빈 결과 제거
        if ("상호" in headerish and ("소재지" in headerish or "주소" in headerish)):
            continue
        if "조회" in headerish and "없" in headerish:
            continue

        # 실제 데이터 row는 보통 3~4컬럼 이상
        if len(cells) >= 3:
            rows.append(cells)
    return rows


def extract_max_page(soup: BeautifulSoup) -> int:
    """
    페이지 하단의 페이징 영역에서 최대 페이지를 추정.
    goPage('N') 형태가 흔함.
    못 찾으면 1.
    """
    html = str(soup)
    nums = [int(x) for x in re.findall(r"goPage\(['\"]?(\d+)['\"]?\)", html)]
    return max(nums) if nums else 1


def fetch_rank_page(session: requests.Session, round_no: int, rank_no: int, page: int) -> BeautifulSoup:
    # 1등은 rankNo 빈값, 2등은 '2'
    rank_val = "" if rank_no == 1 else str(rank_no)

    data = {
        "method": "topStore",
        "nowPage": str(page),
        "rankNo": rank_val,
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


def fetch_rank_rows(session: requests.Session, round_no: int, rank_no: int) -> list[list[str]]:
    """
    ✅ nowPage를 1..N 돌면서 row를 누적.
    (서버가 nowPage를 무시해서 매번 1페이지를 주면, dedup + maxPage로 잡아냄)
    """
    all_rows: list[list[str]] = []
    seen = set()

    first = fetch_rank_page(session, round_no, rank_no, 1)
    max_page = min(extract_max_page(first), MAX_PAGES)

    def consume(soup: BeautifulSoup) -> int:
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
        return new_cnt

    consume(first)

    for p in range(2, max_page + 1):
        soup = fetch_rank_page(session, round_no, rank_no, p)
        new_cnt = consume(soup)

        # 페이지를 넘겼는데 신규 row가 0이면:
        # - 마지막 페이지에 도달했거나
        # - 서버가 nowPage를 무시하고 같은 페이지를 주는 상황
        if new_cnt == 0:
            break

        time.sleep(SLEEP_PER_PAGE)

    return all_rows


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
    r1_rows = fetch_rank_rows(session, round_no, 1)
    r2_rows = fetch_rank_rows(session, round_no, 2)

    print(f"[region] round={round_no} r1_rows={len(r1_rows)} r2_rows={len(r2_rows)}")

    return {
        "rank1": tally(r1_rows),
        "rank2": tally(r2_rows),
    }


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
        time.sleep(0.2)

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
