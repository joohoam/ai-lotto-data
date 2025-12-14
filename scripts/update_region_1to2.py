import json
import os
import re
import time
import datetime
import requests
from bs4 import BeautifulSoup

OUT = "data/region_1to2.json"

# 동행복권 배출점 페이지(회차별)
STORE_URL = "https://dhlottery.co.kr/store.do?method=topStore&drwNo={round}&pageGubun=L645"
HEADERS = {"User-Agent": "Mozilla/5.0"}

API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

RANGE = 40  # ✅ 최근 40회

SIDO_LIST = [
    "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산",
    "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def now_kst_iso():
    kst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(kst).isoformat(timespec="seconds")

def fetch_json(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def guess_latest(start: int = 1200, max_tries: int = 80) -> int:
    cand = start
    for _ in range(max_tries):
        js = fetch_json(API_ROUND.format(round=cand))
        if js.get("returnValue") == "success" and js.get("drwNo") == cand:
            js2 = fetch_json(API_ROUND.format(round=cand + 1))
            if js2.get("returnValue") == "success":
                cand += 1
                continue
            return cand
        cand -= 1
    raise RuntimeError("latestRound 찾기 실패. start 조정 필요")

def load_existing() -> dict:
    if not os.path.exists(OUT):
        return {}
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            j = json.load(f)
            return j if isinstance(j, dict) else {}
    except Exception:
        return {}

def sido_from_address(addr: str) -> str:
    if not addr:
        return "기타"
    a = addr.strip()

    if "인터넷" in a or "동행복권" in a or "dhlottery" in a:
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

    for s in SIDO_LIST:
        if a.startswith(s) or (s in a):
            return s

    return "기타"

def find_rank_tables(soup: BeautifulSoup):
    """
    페이지 내에서 1등/2등 테이블을 찾는다.
    조건: thead/th 중 '소재지' 또는 '주소'가 포함된 테이블만 후보로 사용.
    """
    candidates = []
    for tbl in soup.find_all("table"):
        ths = [th.get_text(" ", strip=True) for th in tbl.select("thead th")]
        if any(("소재지" in t) or ("주소" in t) for t in ths):
            candidates.append((tbl, ths))

    # 등수 섹션 텍스트 근처의 테이블을 우선 찾기
    def find_near(keyword: str):
        node = soup.find(string=re.compile(keyword))
        if not node:
            return None
        p = node.find_parent()
        if not p:
            return None
        # keyword 이후 등장하는 테이블 중 후보만
        for _ in range(6):
            nxt = p.find_next("table")
            if not nxt:
                break
            ths = [th.get_text(" ", strip=True) for th in nxt.select("thead th")]
            if any(("소재지" in t) or ("주소" in t) for t in ths):
                return nxt
            p = nxt
        return None

    t1 = find_near("1등")
    t2 = find_near("2등")

    # fallback: 후보가 2개 이상이면 앞에서 2개를 rank1/rank2로 가정
    if t1 is None or t2 is None:
        if len(candidates) >= 2:
            if t1 is None:
                t1 = candidates[0][0]
            if t2 is None:
                t2 = candidates[1][0]

    return t1, t2

def parse_table_to_counts(table) -> tuple[int, dict]:
    by = {s: 0 for s in SIDO_LIST}
    internet = 0
    other = 0
    total = 0

    if table is None:
        raise RuntimeError("테이블을 찾지 못함")

    # 소재지 컬럼 인덱스 찾기
    ths = [th.get_text(" ", strip=True) for th in table.select("thead th")]
    addr_idx = None
    for i, t in enumerate(ths):
        if "소재지" in t or "주소" in t:
            addr_idx = i
            break
    if addr_idx is None:
        raise RuntimeError("소재지/주소 헤더를 찾지 못함")

    rows = table.select("tbody tr")
    if not rows:
        raise RuntimeError("tbody tr 없음")

    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds or addr_idx >= len(tds):
            continue
        addr = tds[addr_idx]
        sido = sido_from_address(addr)

        total += 1
        if sido in by:
            by[sido] += 1
        elif sido == "인터넷":
            internet += 1
        else:
            other += 1

    return total, {"bySido": by, "internet": internet, "other": other}

def fetch_round_region(rnd: int) -> dict:
    url = STORE_URL.format(round=rnd)
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()

    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"

    html = r.text
    soup = BeautifulSoup(html, "lxml")

    t1, t2 = find_rank_tables(soup)

    total1, c1 = parse_table_to_counts(t1)
    total2, c2 = parse_table_to_counts(t2)

    # ✅ “전부 0 + other만 증가” 같은 경우는 파싱 실패로 간주하고 저장하지 않음
    if total1 > 0 and sum(c1["bySido"].values()) == 0 and c1["other"] == total1:
        raise RuntimeError(f"rank1 파싱 실패 의심(total={total1}, other={c1['other']})")
    if total2 > 0 and sum(c2["bySido"].values()) == 0 and c2["other"] == total2:
        raise RuntimeError(f"rank2 파싱 실패 의심(total={total2}, other={c2['other']})")

    return {
        "rank1": {"totalStores": total1, **c1},
        "rank2": {"totalStores": total2, **c2},
    }

def main():
    ensure_dirs()
    existing = load_existing()

    # 시작점
    start_guess = 1200
    try:
        start_guess = int((existing.get("meta", {}) or {}).get("latestRound", start_guess))
    except Exception:
        pass

    latest = guess_latest(start=start_guess)
    start_round = max(1, latest - RANGE + 1)

    rounds = existing.get("rounds", {})
    if not isinstance(rounds, dict):
        rounds = {}

    # 윈도우 밖 회차 삭제
    pruned = {}
    for k, v in rounds.items():
        if isinstance(k, str) and k.isdigit() and isinstance(v, dict):
            rn = int(k)
            if start_round <= rn <= latest:
                pruned[k] = v
    rounds = pruned

    for rnd in range(start_round, latest + 1):
        key = str(rnd)

        # 이미 값이 있으면 스킵(필요하면 force 로직 추가 가능)
        if key in rounds and isinstance(rounds[key], dict) and "rank1" in rounds[key] and "rank2" in rounds[key]:
            continue

        try:
            rounds[key] = fetch_round_region(rnd)
        except Exception as e:
            # ✅ 실패하면 해당 회차는 기존 값이 있으면 유지, 없으면 그냥 건너뜀
            print(f"[region] round {rnd} skipped: {e}")
        time.sleep(0.25)

    out = {
        "meta": {"latestRound": latest, "range": RANGE, "updatedAt": now_kst_iso()},
        "rounds": {k: rounds[k] for k in sorted(rounds.keys(), key=lambda x: int(x))},
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
