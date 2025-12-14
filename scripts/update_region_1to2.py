
import json
import os
import re
import time
import datetime
import requests
from bs4 import BeautifulSoup

OUT = "data/region_1to2.json"

# 동행복권 당첨판매점(회차별) 페이지
STORE_URL = "https://dhlottery.co.kr/store.do?method=topStore&drwNo={round}&pageGubun=L645"
API_LATEST = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ✅ 최근 몇 회 저장할지
WINDOW = 40

SIDO_LIST = [
    "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산",
    "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def fetch_json(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def get_latest_round_guess(start: int = 1200, max_tries: int = 60) -> int:
    """
    존재하는 최신 회차를 API로 탐색해서 찾음 (없는 회차는 returnValue != success)
    """
    cand = start
    for _ in range(max_tries):
        js = fetch_json(API_LATEST.format(round=cand))
        if js.get("returnValue") == "success" and js.get("drwNo") == cand:
            js2 = fetch_json(API_LATEST.format(round=cand + 1))
            if js2.get("returnValue") == "success":
                cand += 1
                continue
            return cand
        cand -= 1
    raise RuntimeError("latestRound를 찾지 못했습니다. start 값을 조정하세요.")

def sido_from_address(addr: str) -> str:
    """
    판매점 '소재지'에서 시/도를 뽑음.
    인터넷 판매(동행복권) 같은 케이스는 '인터넷'으로 분류.
    """
    if not addr:
        return "기타"

    if "동행복권" in addr or "dhlottery.co.kr" in addr or "인터넷" in addr:
        return "인터넷"

    a = addr.strip()

    # '서울특별시', '경기도' 등 대응
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

    # 그냥 '서울', '경기'로 시작하는 경우
    for s in SIDO_LIST:
        if a.startswith(s):
            return s

    return "기타"

def find_rank_table(soup: BeautifulSoup, rank_text: str):
    """
    '1등 배출점', '2등 배출점' 텍스트 근처의 table을 찾아 반환.
    구조가 바뀌면 fallback으로 테이블 순서 사용.
    """
    node = soup.find(string=re.compile(rank_text))
    if node:
        tbl = node.find_parent().find_next("table")
        if tbl:
            return tbl
    # fallback: tbl_data 테이블들
    tables = soup.select("table.tbl_data")
    if len(tables) >= 2:
        return tables[0] if "1등" in rank_text else tables[1]
    # 마지막 fallback: 그냥 첫/둘째 table
    tables2 = soup.find_all("table")
    if len(tables2) >= 2:
        return tables2[0] if "1등" in rank_text else tables2[1]
    return None

def parse_table_to_counts(table) -> tuple[int, dict]:
    """
    테이블에서 '소재지' 컬럼을 읽어 지역 카운트 집계.
    반환: (총 row 수, bySido dict)
    """
    by = {s: 0 for s in SIDO_LIST}
    by_internet = 0
    by_other = 0

    if table is None:
        return 0, {"bySido": by, "internet": 0, "other": 0}

    rows = table.select("tbody tr") or table.select("tr")
    total = 0
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds:
            continue

        # 소재지는 보통 마지막/중간 컬럼에 존재. 가장 긴 텍스트를 소재지 후보로 사용.
        addr = max(tds, key=len) if tds else ""
        sido = sido_from_address(addr)

        total += 1
        if sido in by:
            by[sido] += 1
        elif sido == "인터넷":
            by_internet += 1
        else:
            by_other += 1

    return total, {"bySido": by, "internet": by_internet, "other": by_other}

def load_existing():
    if not os.path.exists(OUT):
        return {}
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            j = json.load(f)
            return j if isinstance(j, dict) else {}
    except Exception:
        return {}

def prune_rounds(rounds: dict, start_round: int, latest_round: int) -> dict:
    pruned = {}
    for k, v in rounds.items():
        if not isinstance(k, str) or not k.isdigit():
            continue
        rn = int(k)
        if start_round <= rn <= latest_round:
            pruned[k] = v
    return pruned

def fetch_round_region(round_no: int) -> dict:
    r = requests.get(STORE_URL.format(round=round_no), headers=HEADERS, timeout=25)
    r.raise_for_status()

    # 인코딩 이슈 대비 (사이트가 EUC-KR로 내려주는 경우가 있음)
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

    latest = get_latest_round_guess()
    start = max(1, latest - WINDOW + 1)

    rounds = existing.get("rounds", {})
    if not isinstance(rounds, dict):
        rounds = {}

    # 최근 WINDOW 범위 밖 제거
    rounds = prune_rounds(rounds, start, latest)

    for rnd in range(start, latest + 1):
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
