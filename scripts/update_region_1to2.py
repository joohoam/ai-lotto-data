import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

OUT = "data/region_1to2.json"

# ✅ topStore 엔드포인트 (pageGubun은 쿼리로 고정)
TOPSTORE_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645"

# 최신 회차 확인용 API (회차 존재 여부 체크)
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://dhlottery.co.kr/",
}

# ✅ 운영 파라미터 (필요하면 Actions env로 조정 가능)
RANGE = int(os.getenv("REGION_RANGE", "5"))  # 기본 5회만 갱신 (속도/안정)
MAX_PAGES = int(os.getenv("REGION_MAX_PAGES", "80"))
SLEEP_PER_PAGE = float(os.getenv("REGION_SLEEP_PER_PAGE", "0.15"))
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


def fetch_json(session: requests.Session, url: str, timeout=20) -> dict:
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def is_success_round(d: dict) -> bool:
    return (d.get("returnValue") == "success") and isinstance(d.get("drwNo"), int) and d.get("drwNo", 0) > 0


def get_latest_round_guess(session: requests.Session, max_tries: int = 80) -> int:
    """
    1) 기존 파일 meta.latestRound가 있으면 그 근처에서 탐색
    2) getLottoNumber API로 존재여부를 확인하면서 최신회차를 찾음
    """
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

    raise RuntimeError("latestRound를 찾지 못했습니다. start 값을 조정해 주세요.")


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def detect_sido(addr: str) -> Optional[str]:
    if not addr:
        return None
    a = normalize_text(addr)
    m = SIDO_RE.match(a)
    return m.group(1) if m else None


def extract_address_from_row_cells(cells: list[str]) -> Optional[str]:
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


def fetch_html_post(session: requests.Session, data: dict) -> str:
    """
    ✅ rankNo/nowPage를 안정적으로 적용하기 위해 POST + form-data로 조회
    """
    r = session.post(TOPSTORE_URL, headers=HEADERS, data=data, timeout=TIMEOUT)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding
    return r.text


def find_store_table(soup: BeautifulSoup):
    """
    페이지 내 여러 table 중 '당첨 판매점' 목록 테이블을 찾는다.
    (헤더에 '상호' + '소재지/주소'가 있는 테이블 우선)
    """
    tables = soup.find_all("table")
    best = None
    best_score = -1

    for tb in tables:
        header_text = normalize_text(tb.get_text(" ", strip=True))
        score = 0
        if "상호" in header_text:
            score += 2
        if "소재지" in header_text or "주소" in header_text:
            score += 2
        if "번호선택구분" in header_text or "구분" in header_text:
            score += 1

        trs = tb.find_all("tr")
        score += min(len(trs), 30) * 0.05

        if score > best_score:
            best_score = score
            best = tb

    return best


def parse_rows_from_table(tb) -> list[list[str]]:
    if tb is None:
        return []

    rows = []
    for tr in tb.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if not tds:
            continue
        cells = [normalize_text(td.get_text(" ", strip=True)) for td in tds]
        headerish = " ".join(cells)

        # 헤더 제거
        if ("상호" in headerish and ("소재지" in headerish or "주소" in headerish)):
            continue

        # 조회 없음 문구 제거
        if "조회된" in headerish and "없" in headerish:
            continue

        if len(cells) >= 3:
            rows.append(cells)

    return rows


def fetch_rank_rows(session: requests.Session, round_no: int, rank_no: int) -> list[list[str]]:
    """
    ✅ rankNo(1/2) + nowPage(1..N)을 돌면서 모든 row를 누적한다.
    중복 방지 위해 row 문자열 키로 dedup.
    """
    seen = set()
    all_rows: list[list[str]] = []

    for page in range(1, MAX_PAGES + 1):
        html = fetch_html_post(
            session,
            {
                "drwNo": str(round_no),
                "rankNo": str(rank_no),
                "nowPage": str(page),
                # 아래 키들은 없어도 되는 경우가 많지만, 페이지에 따라 요구될 수 있어 같이 보냄
                "pageGubun": "L645",
                "method": "topStore",
            },
        )
        soup = BeautifulSoup(html, "lxml")
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

        # 더 이상 신규 row가 없으면 종료
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


def fetch_round_region(session: requests.Session, round_no: int) -> dict:
    r1_rows = fetch_rank_rows(session, round_no, 1)
    r2_rows = fetch_rank_rows(session, round_no, 2)

    print(f"[region] round={round_no} r1_rows={len(r1_rows)} r2_rows={len(r2_rows)}")

    rank1 = tally(r1_rows)
    rank2 = tally(r2_rows)

    return {"rank1": rank1, "rank2": rank2}


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

    # ✅ 최근 RANGE 회만 갱신 (그 외는 유지)
    for rnd in range(start_round, latest + 1):
        try:
            rounds_obj[str(rnd)] = fetch_round_region(session, rnd)
        except Exception as e:
            print(f"[region] ERROR round={rnd}: {e}")
        time.sleep(0.25)

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
