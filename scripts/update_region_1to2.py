import json
import os
import re
import time
import datetime
import requests
from bs4 import BeautifulSoup

OUT = "data/region_1to2.json"

# 최신 회차 탐색용(공식 API)
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

# ✅ 지역 요약을 제공하는 페이지(안정적으로 region count만 뽑음)
# (주소 테이블 파싱보다 실패율이 낮음)
SOURCE_URL = "https://lottobomb.com/winning-region/index/{round}"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

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

def load_existing() -> dict:
    if not os.path.exists(OUT):
        return {}
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            j = json.load(f)
            return j if isinstance(j, dict) else {}
    except Exception:
        return {}

def get_latest_round_guess(start: int = 1200, max_tries: int = 100) -> int:
    """
    존재하는 최신 회차를 공식 API로 탐색해서 찾음.
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
        cand -= 1
    raise RuntimeError("latestRound를 찾지 못했습니다. start 값을 조정하세요.")

def _init_counts():
    return {s: 0 for s in SIDO_LIST}

def _parse_region_list_items(text: str) -> tuple[str, int] | None:
    """
    예: '서울 3 개', '경기 18 개', '온라인 1 개'
    """
    t = re.sub(r"\s+", " ", (text or "").strip())
    m = re.match(r"^(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주|온라인|인터넷)\s+(\d+)\s*개", t)
    if not m:
        return None
    return m.group(1), int(m.group(2))

def _extract_rank_region_counts(html: str, rank: int) -> dict:
    """
    lottobomb 페이지에서 'N등 당첨 지역' 섹션의 bullet 리스트를 파싱.
    결과는 rank1/rank2 구조에 맞춰 반환.
    """
    soup = BeautifulSoup(html, "lxml")

    # 섹션 텍스트(예: "2등 당첨 지역")
    pattern = re.compile(rf"{rank}\s*등\s*당첨\s*지역")

    # 해당 텍스트를 포함하는 노드 찾기
    node = soup.find(string=pattern)
    if not node:
        # 일부 페이지는 '로또 #### 회 2등 당첨 지역' 형태이므로 넓게 탐색
        node = soup.find(string=re.compile(rf"로또.*{rank}\s*등\s*당첨\s*지역"))
    if not node:
        raise RuntimeError(f"rank{rank} 섹션을 찾지 못했습니다.")

    # node 이후에 나오는 ul/ol에서 li를 읽음
    parent = node.find_parent()
    lst = None
    if parent:
        lst = parent.find_next(["ul", "ol"])
    if not lst:
        raise RuntimeError(f"rank{rank} 리스트를 찾지 못했습니다.")

    by_sido = _init_counts()
    internet = 0
    other = 0
    total = 0

    for li in lst.find_all("li"):
        item = li.get_text(" ", strip=True)
        parsed = _parse_region_list_items(item)
        if not parsed:
            continue
        region, cnt = parsed
        total += cnt
        if region in by_sido:
            by_sido[region] += cnt
        elif region in ("온라인", "인터넷"):
            internet += cnt
        else:
            other += cnt

    return {
        "totalStores": total,
        "bySido": by_sido,
        "internet": internet,
        "other": other,
    }

def fetch_round_region(round_no: int) -> dict:
    url = SOURCE_URL.format(round=round_no)
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()

    # 인코딩 대비
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"

    html = r.text

    rank1 = _extract_rank_region_counts(html, 1)
    rank2 = _extract_rank_region_counts(html, 2)

    return {
        "rank1": rank1,
        "rank2": rank2,
    }

def main():
    ensure_dirs()
    existing = load_existing()

    # 기존 meta.latestRound가 있으면 그 근처부터 탐색
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

    # 윈도우 밖 데이터 제거
    pruned = {}
    for k, v in rounds.items():
        if isinstance(k, str) and k.isdigit():
            rn = int(k)
            if start_round <= rn <= latest and isinstance(v, dict):
                pruned[k] = v
    rounds = pruned

    # 윈도우 범위 채우기(없거나 불완전하면 재생성)
    for rnd in range(start_round, latest + 1):
        key = str(rnd)
        needs = True
        if key in rounds and isinstance(rounds[key], dict):
            if "rank1" in rounds[key] and "rank2" in rounds[key]:
                r1 = rounds[key]["rank1"]
                r2 = rounds[key]["rank2"]
                # totalStores가 0이면 비정상으로 보고 재생성
                if isinstance(r1, dict) and isinstance(r2, dict):
                    if int(r1.get("totalStores", 0)) > 0 or int(r2.get("totalStores", 0)) > 0:
                        needs = False

        if not needs:
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
