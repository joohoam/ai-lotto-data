import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

OUT = "data/region_1to2.json"

POST_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645"
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://dhlottery.co.kr/",
    "Origin": "https://dhlottery.co.kr",
}

RANGE = int(os.getenv("REGION_RANGE", "10"))

# 튜닝 파라미터
MAX_PAGES = int(os.getenv("REGION_MAX_PAGES", "220"))  # 2등 다페이지 대비
SLEEP_PER_PAGE = float(os.getenv("REGION_SLEEP_PER_PAGE", "0.10"))
TIMEOUT = int(os.getenv("REGION_TIMEOUT", "25"))

HTTP_RETRY_TOTAL = int(os.getenv("REGION_HTTP_RETRY_TOTAL", "6"))
HTTP_BACKOFF = float(os.getenv("REGION_HTTP_BACKOFF", "0.8"))

SORT_DESC = os.getenv("REGION_SORT_DESC", "1") == "1"

SIDO_LIST = [
    "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산",
    "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]
SIDO_RE = re.compile(r"^(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)\b")

# 온라인(인터넷) 판정 키워드
ONLINE_STORE_NAME_KEYWORDS = ["인터넷 복권판매사이트"]
ONLINE_ADDR_KEYWORDS = ["dhlottery.co.kr", "동행복권(dhlottery.co.kr)", "동행복권 (dhlottery.co.kr)"]

# 섹션 라벨 감지(표 안/밖 어디서든 등장할 수 있음)
RANK_LABEL_RE = re.compile(r"([12])\s*등\s*배출점")


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


def infer_rank_for_table(tb: Tag) -> Optional[int]:
    """
    테이블 바로 위(가까운 라벨)에서 '1등 배출점'/'2등 배출점'을 찾아
    이 table이 어느 등수 섹션인지 판정한다.
    """
    el = tb.find_previous(["h1", "h2", "h3", "h4", "h5", "strong", "p", "span", "div", "li", "a"])
    steps = 0
    while el is not None and steps < 250:
        steps += 1
        txt = normalize_text(el.get_text(" ", strip=True))
        if txt:
            m = RANK_LABEL_RE.search(txt)
            if m:
                return int(m.group(1))
        el = el.find_previous(["h1", "h2", "h3", "h4", "h5", "strong", "p", "span", "div", "li", "a"])
    return None


def find_rank_table(soup: BeautifulSoup, rank_no: int) -> Optional[Tag]:
    """
    ✅ 라벨 기반으로만 테이블 선택: 2등이 1등으로 섞이는 사고 구조적으로 차단
    """
    best: Optional[Tag] = None
    best_score = -1.0

    for tb in soup.find_all("table"):
        r = infer_rank_for_table(tb)
        if r != int(rank_no):
            continue

        txt = normalize_text(tb.get_text(" ", strip=True))
        score = 0.0
        if "상호" in txt:
            score += 2.0
        if "소재지" in txt or "주소" in txt:
            score += 2.0
        score += min(len(tb.select("tbody tr")), 300) / 100.0

        if score > best_score:
            best_score = score
            best = tb

    return best


def extract_max_page_for_rank(soup: BeautifulSoup, rank_no: int, tb: Tag) -> Optional[int]:
    """
    ✅ rank 테이블 이후 ~ 다음 등수 섹션 전 범위에서 paginate를 찾는다.
    (2등 다페이지 대응)
    """
    other_rank = 2 if int(rank_no) == 1 else 1
    stop_re = re.compile(rf"{other_rank}\s*등\s*배출점")

    checked = 0
    for el in tb.next_elements:
        checked += 1
        if checked > 3000:
            break

        if isinstance(el, Tag):
            if el.name in ["h1", "h2", "h3", "h4", "h5", "strong", "p", "span", "div", "li", "a"]:
                txt = normalize_text(el.get_text(" ", strip=True))
                if txt and stop_re.search(txt):
                    break

            if el.name == "div":
                cls = " ".join(el.get("class", []) or "")
                if any(k in cls for k in ["paginate", "paging", "pagination", "page", "paginate_common"]):
                    txt = normalize_text(el.get_text(" ", strip=True))
                    nums = [int(x) for x in re.findall(r"\b(\d{1,4})\b", txt)]
                    return max(nums) if nums else None

    return None


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
    r.raise_for_status()

    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding

    return BeautifulSoup(r.text, "html.parser")


def parse_rows_from_table_for_rank(tb: Tag, rank_no: int) -> List[List[str]]:
    """
    ✅ 표 내부에서 섹션이 바뀌는 신호(예: '2등 배출점')가 등장하면 즉시 중단
    => 같은 페이지 내 아래쪽 2등 영역이 1등으로 섞이는 현상 차단
    """
    rows: List[List[str]] = []
    other_rank = 2 if int(rank_no) == 1 else 1
    stop_re = re.compile(rf"{other_rank}\s*등\s*배출점")

    for tr in tb.find_all("tr"):
        txt_tr = normalize_text(tr.get_text(" ", strip=True))
        if txt_tr and stop_re.search(txt_tr):
            break

        tds = tr.find_all(["td", "th"])
        if not tds:
            continue

        cells = [normalize_text(td.get_text(" ", strip=True)) for td in tds]
        headerish = " ".join(cells)

        # 헤더/빈행 제외
        if ("상호" in headerish and ("소재지" in headerish or "주소" in headerish)):
            continue
        if "조회" in headerish and "없" in headerish:
            continue

        if len(cells) >= 3:
            rows.append(cells)

    return rows


def fetch_rank_rows(session: requests.Session, round_no: int, rank_no: int) -> List[List[str]]:
    """
    ✅ 1등: 무조건 page=1만 요청(사이트 구조상 1등은 한 페이지에 전부)
    ✅ 2등: paginate 기반 수집(15개/페이지)
    """
    # 1등은 페이지네이션이 없다고 봄(구조 기반 확정)
    if int(rank_no) == 1:
        soup = fetch_rank_page(session, round_no, rank_no, 1)
        tb = find_rank_table(soup, 1)
        if tb is None:
            print(f"[region] WARN round={round_no} rank=1: rank table not found on page1")
            return []
        return parse_rows_from_table_for_rank(tb, 1)

    # 2등(다페이지)
    seen = set()
    all_rows: List[List[str]] = []

    last_page_hint: Optional[int] = None
    prev_page_signature: Optional[str] = None

    for page in range(1, MAX_PAGES + 1):
        soup = fetch_rank_page(session, round_no, 2, page)

        tb = find_rank_table(soup, 2)
        if tb is None:
            if page == 1:
                print(f"[region] WARN round={round_no} rank=2: rank table not found on page1")
            break

        if page == 1:
            last_page_hint = extract_max_page_for_rank(soup, 2, tb)

        rows = parse_rows_from_table_for_rank(tb, 2)

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

        if new_cnt == 0:
            break

        if last_page_hint is not None and page >= last_page_hint:
            break

        time.sleep(SLEEP_PER_PAGE)

    return all_rows


def is_online_row(cells: List[str]) -> bool:
    store_name = normalize_text(cells[1]) if len(cells) >= 2 else ""
    addr = extract_address_from_row_cells(cells) or (normalize_text(cells[2]) if len(cells) >= 3 else "")
    joined = normalize_text(" ".join(cells))

    if any(k in store_name for k in ONLINE_STORE_NAME_KEYWORDS):
        return True

    if any(k in addr for k in ONLINE_ADDR_KEYWORDS):
        return True
    if "dhlottery.co.kr" in addr:
        return True

    if "인터넷" in joined and "dhlottery" in joined:
        return True

    return False


def tally(rows: List[List[str]]) -> Dict[str, Any]:
    by_sido = {s: 0 for s in SIDO_LIST}
    internet = 0
    other = 0
    total = 0

    for cells in rows:
        total += 1
        if is_online_row(cells):
            internet += 1
            continue

        addr = extract_address_from_row_cells(cells) or ""
        s = detect_sido(addr)
        if s:
            by_sido[s] += 1
        else:
            other += 1

    return {
        "totalStores": total,
        "bySido": by_sido,
        "internet": internet,
        "other": other,
        "offlineTotal": total - internet,
        "onlineTotal": internet,
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
