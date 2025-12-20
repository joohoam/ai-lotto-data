import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

import requests
from bs4 import BeautifulSoup

# urllib3 Retry (requests가 내부적으로 사용)
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

OUT = "data/region_1to2.json"

POST_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645"
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://dhlottery.co.kr/",
}

# ✅ 매번 최신 N회 전체 재수집
RANGE = int(os.getenv("REGION_RANGE", "40"))

# 튜닝 파라미터 (env로 조정 가능)
MAX_PAGES = int(os.getenv("REGION_MAX_PAGES", "220"))
SLEEP_PER_PAGE = float(os.getenv("REGION_SLEEP_PER_PAGE", "0.10"))
TIMEOUT = int(os.getenv("REGION_TIMEOUT", "25"))

# 네트워크 재시도(HTTP 429/5xx 등에 자동 백오프)
HTTP_RETRY_TOTAL = int(os.getenv("REGION_HTTP_RETRY_TOTAL", "6"))
HTTP_BACKOFF = float(os.getenv("REGION_HTTP_BACKOFF", "0.8"))

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


def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=HTTP_RETRY_TOTAL,
        connect=HTTP_RETRY_TOTAL,
        read=HTTP_RETRY_TOTAL,
        backoff_factor=HTTP_BACKOFF,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def fetch_json(session: requests.Session, url: str, timeout=20) -> dict:
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def is_success_round(d: dict) -> bool:
    return (d.get("returnValue") == "success") and isinstance(d.get("drwNo"), int) and d.get("drwNo", 0) > 0


def get_latest_round_guess(session: requests.Session, max_tries: int = 150) -> int:
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
                time.sleep(0.08)
                continue
            return cand
        cand -= 1
        time.sleep(0.08)

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


def find_store_table_fallback(soup: BeautifulSoup):
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


def find_rank_table(soup: BeautifulSoup, rank_no: int):
    """
    ✅ 등수(1/2) 라벨 섹션을 기준으로 해당 테이블을 선택
    """
    rank = str(rank_no)
    best = None
    best_n = -1

    for tag in soup.find_all(["h2", "h3", "h4", "h5", "strong", "p", "span", "div", "li", "a"]):
        txt = normalize_text(tag.get_text(" ", strip=True))
        if not txt:
            continue

        if (f"{rank}등" in txt or f"{rank} 등" in txt) and ("배출" in txt or "판매점" in txt or "당첨" in txt):
            tb = tag.find_next("table")
            if tb is None:
                continue
            n = len(tb.select("tbody tr")) or len(tb.find_all("tr"))
            if n > best_n:
                best_n = n
                best = tb

    return best


def extract_max_page(soup: BeautifulSoup) -> Optional[int]:
    """
    ✅ 페이지네이션이 있으면 마지막 페이지를 추정해서 불필요한 요청을 줄임
    """
    candidates = []
    for sel in ["div.paginate", "div.paging", "div.pagination", "div.page", "div.paginate_common"]:
        div = soup.select_one(sel)
        if div:
            candidates.append(div)
    if not candidates:
        # class가 애매한 경우: 숫자 링크가 몰려있는 div 탐색
        for div in soup.find_all("div"):
            txt = normalize_text(div.get_text(" ", strip=True))
            if txt and re.search(r"\b1\b", txt) and re.search(r"\b2\b", txt):
                candidates.append(div)

    best_max = None
    for div in candidates:
        txt = normalize_text(div.get_text(" ", strip=True))
        nums = [int(x) for x in re.findall(r"\b(\d{1,4})\b", txt)]
        if nums:
            m = max(nums)
            if best_max is None or m > best_max:
                best_max = m

    return best_max


def fetch_rank_page(session: requests.Session, round_no: int, rank_no: int, page: int) -> BeautifulSoup:
    data = {
        "method": "topStore",
        "nowPage": str(page),
        "rankNo": str(rank_no),
        "rank": str(rank_no),
        "gameNo": "5133",
        "drwNo": str(round_no),
        "schKey": "all",
        "schVal": "",
    }

    r = session.post(POST_URL, headers=HEADERS, data=data, timeout=TIMEOUT)

    # Retry 어댑터가 status code는 재시도하지만, 최종적으로 4xx/5xx가 남을 수 있음
    r.raise_for_status()

    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding

    return BeautifulSoup(r.text, "lxml")


def fetch_rank_rows(session: requests.Session, round_no: int, rank_no: int) -> List[List[str]]:
    seen = set()
    all_rows: List[List[str]] = []

    last_page_hint: Optional[int] = None
    prev_page_signature: Optional[str] = None

    for page in range(1, MAX_PAGES + 1):
        soup = fetch_rank_page(session, round_no, rank_no, page)

        if page == 1:
            last_page_hint = extract_max_page(soup)

        tb = find_rank_table(soup, rank_no) or find_store_table_fallback(soup)
        rows = parse_rows_from_table(tb)

        # ✅ “페이지가 바뀌지 않고 같은 내용만 반복되는” 케이스 빠른 종료
        signature = "|".join(["#".join(r) for r in rows[:10]])
        if prev_page_signature is not None and signature == prev_page_signature:
            break
        prev_page_signature = signature

        new_cnt = 0
        for cells in rows:
            key = "|".join(cells)
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(cells)
            new_cnt += 1

        # 신규가 없으면 종료
        if new_cnt == 0:
            break

        # 페이지네이션 힌트가 있으면 그 이상은 안 감
        if last_page_hint is not None and page >= last_page_hint:
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


def main():
    ensure_dirs()
    session = build_session()

    latest = get_latest_round_guess(session)
    start_round = max(1, latest - (RANGE - 1))

    rounds_obj: Dict[str, Any] = {}

    for rnd in range(start_round, latest + 1):
        try:
            rounds_obj[str(rnd)] = fetch_round_region(session, rnd)
        except Exception as e:
            # 개별 회차 실패해도 다음 회차로 진행(전량 재수집에서 안정성 우선)
            print(f"[region] ERROR round={rnd}: {e}")
        time.sleep(0.08)

    keys = sorted(rounds_obj.keys(), key=lambda x: int(x), reverse=SORT_DESC)
    rounds_obj = {k: rounds_obj[k] for k in keys}

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
