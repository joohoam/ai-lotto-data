import json
import os
import re
import time
import datetime
import requests
from bs4 import BeautifulSoup

# ✅ 출력 파일 (레포에 이미 있는 파일명과 동일하게)
OUT = "data/region_1to2.json"

# ✅ 회차별 당첨판매점(배출점) 페이지
# 페이지 구조가 바뀔 수 있어서 파싱을 최대한 방어적으로 처리
STORE_URL = "https://dhlottery.co.kr/store.do?method=topStore&drwNo={round}&pageGubun=L645"

# ✅ 최신 회차 탐색용 공식 API
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ✅ 최근 N회(사용자 요구: 40회)
WINDOW = 40

SIDO_LIST = [
    "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산",
    "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]

# 주소(소재지)에서 시/도 식별에 쓰는 키워드
SIDO_KEYWORDS = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원도", "강원특별자치도",
    "충청북도", "충청남도", "전라북도", "전북특별자치도", "전라남도",
    "경상북도", "경상남도", "제주도", "제주특별자치도"
]

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def fetch_json(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def load_existing() -> dict:
    if not os.path.exists(OUT):
        return {}
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            j = json.load(f)
            return j if isinstance(j, dict) else {}
    except Exception:
        return {}

def get_latest_round_guess(start: int = 1200, max_tries: int = 80) -> int:
    """
    존재하는 최신 회차를 API로 탐색해서 찾음.
    start는 대략 최근 회차 근처로 두면 빠름.
    """
    cand = start
    for _ in range(max_tries):
        js = fetch_json(API_ROUND.format(round=cand))
        if js.get("returnValue") == "success" and js.get("drwNo") == cand:
            js2 = fetch_json(API_ROUND.format(round=cand + 1))
            if js2.get("returnValue") == "success":
                cand += 1
                continue
            return cand
        # 실패하면 뒤로
        cand -= 1
    raise RuntimeError("latestRound를 찾지 못했습니다. start 값을 조정하세요.")

def sido_from_address(addr: str) -> str:
    """
    판매점 소재지(주소) 문자열에서 시/도를 추출.
    """
    if not addr:
        return "기타"

    a = addr.strip()

    # 인터넷/동행복권 케이스
    if "동행복권" in a or "dhlottery" in a or "인터넷" in a:
        return "인터넷"

    mapping = {
        "서울특별시": "서울",
        "부산광역시": "부산",
        "대구광역시": "대구",
        "인천광역시": "인천",
        "광주광역시": "광주",
        "대전광역시": "대전",
        "울산광역시": "울산",
        "세종특별자치시": "세종",
        "경기도": "경기",
        "강원특별자치도": "강원",
        "강원도": "강원",
        "충청북도": "충북",
        "충청남도": "충남",
        "전북특별자치도": "전북",
        "전라북도": "전북",
        "전라남도": "전남",
        "경상북도": "경북",
        "경상남도": "경남",
        "제주특별자치도": "제주",
        "제주도": "제주",
    }

    for k, v in mapping.items():
        if a.startswith(k):
            return v

    # 간단 시작 매칭
    for s in SIDO_LIST:
        if a.startswith(s):
            return s

    # 중간에라도 시/도 키워드가 들어 있으면 탐지 (예: "(서울) ..." 같은 형태)
    for s in SIDO_LIST:
        if s in a:
            return s

    return "기타"

def find_rank_table(soup: BeautifulSoup, rank_text: str):
    """
    '1등 배출점', '2등 배출점' 근처의 테이블을 찾음.
    실패하면 tbl_data 테이블 순서로 fallback.
    """
    node = soup.find(string=re.compile(rank_text))
    if node:
        # node 기준으로 다음 table
        parent = node.find_parent()
        if parent:
            tbl = parent.find_next("table")
            if tbl:
                return tbl

    tables = soup.select("table.tbl_data")
    if len(tables) >= 2:
        return tables[0] if "1등" in rank_text else tables[1]

    # 최후 fallback
    tables2 = soup.find_all("table")
    if len(tables2) >= 2:
        return tables2[0] if "1등" in rank_text else tables2[1]

    return None

def guess_address_from_tds(tds: list[str], addr_idx: int | None) -> str:
    """
    주소(td)를 robust하게 선택.
    1) 헤더에서 찾은 addr_idx가 있으면 우선 사용
    2) 아니면 td들 중 시/도 키워드/행정구역 패턴이 있는 값을 우선 선택
    3) 그래도 없으면 마지막 td 또는 가장 긴 td fallback
    """
    if addr_idx is not None and 0 <= addr_idx < len(tds):
        return tds[addr_idx]

    # 시/도 키워드가 들어간 td 후보 우선
    candidates = []
    for t in tds:
        if any(k in t for k in SIDO_KEYWORDS):
            candidates.append(t)
        elif re.search(r"(특별시|광역시|특별자치시|도|특별자치도)", t):
            candidates.append(t)

    if candidates:
        # 후보가 여러 개면 가장 긴 텍스트를 주소로 간주
        return max(candidates, key=len)

    # 흔히 주소가 마지막 칼럼인 경우가 많음
    if tds:
        return tds[-1]

    return ""

def parse_table_to_counts(table) -> tuple[int, dict]:
    """
    테이블에서 '소재지(주소)'를 읽어 지역 카운트 집계.
    반환: (총 row 수, {"bySido":..., "internet":..., "other":...})
    """
    by = {s: 0 for s in SIDO_LIST}
    by_internet = 0
    by_other = 0

    if table is None:
        return 0, {"bySido": by, "internet": 0, "other": 0}

    # 1) 헤더에서 "소재지/주소" 컬럼 인덱스 탐색
    addr_idx = None
    ths = [th.get_text(" ", strip=True) for th in table.select("thead th")]
    for i, t in enumerate(ths):
        if "소재지" in t or "주소" in t:
            addr_idx = i
            break

    rows = table.select("tbody tr") or table.select("tr")
    total = 0

    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds:
            continue

        addr = guess_address_from_tds(tds, addr_idx)
        sido = sido_from_address(addr)

        total += 1
        if sido in by:
            by[sido] += 1
        elif sido == "인터넷":
            by_internet += 1
        else:
            by_other += 1

    return total, {"bySido": by, "internet": by_internet, "other": by_other}

def fetch_round_region(round_no: int) -> dict:
    r = requests.get(STORE_URL.format(round=round_no), headers=HEADERS, timeout=25)
    r.raise_for_status()

    # 인코딩 이슈 대비
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"

    soup = BeautifulSoup(r.text, "lxml")

    t1 = find_rank_table(soup, "1등 배출점")
    t2 = find_rank_table(soup, "2등 배출점")

    total1, c1 = parse_table_to_counts(t1)
    total2, c2 = parse_table_to_counts(t2)

    return {
        "rank1": {
            "totalStores": total1,
            "bySido": c1["bySido"],
            "internet": c1["internet"],
            "other": c1["other"],
        },
        "rank2": {
            "totalStores": total2,
            "bySido": c2["bySido"],
            "internet": c2["internet"],
            "other": c2["other"],
        },
    }

def main():
    ensure_dirs()
    existing = load_existing()

    # start 기준을 기존 파일 meta.latestRound로 잡으면 더 빨라짐
    start_guess = 1200
    try:
        prev_latest = int((existing.get("meta", {}) or {}).get("latestRound", 0))
        if prev_latest > 0:
            start_guess = prev_latest
    except Exception:
        pass

    latest = get_latest_round_guess(start=start_guess)
    start_round = max(1, latest - WINDOW + 1)

    rounds = existing.get("rounds", {})
    if not isinstance(rounds, dict):
        rounds = {}

    # WINDOW 범위 밖 삭제
    pruned = {}
    for k, v in rounds.items():
        if isinstance(k, str) and k.isdigit():
            rn = int(k)
            if start_round <= rn <= latest and isinstance(v, dict):
                pruned[k] = v
    rounds = pruned

    # 필요한 회차만 채우기
    for rnd in range(start_round, latest + 1):
        key = str(rnd)
        if key in rounds and isinstance(rounds[key], dict) and "rank1" in rounds[key] and "rank2" in rounds[key]:
            continue

        rounds[key] = fetch_round_region(rnd)
        time.sleep(0.25)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")

    out = {
        "meta": {
            "latestRound": latest,
            "range": WINDOW,
            "updatedAt": now,
        },
        "rounds": {k: rounds[k] for k in sorted(rounds.keys(), key=lambda x: int(x))},
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()

