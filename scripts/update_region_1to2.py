import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

# 출력 파일
OUT = "data/region_1to2.json"

# 동행복권 배출점 페이지(로또6/45)
STORE_URL = "https://dhlottery.co.kr/store.do?method=topStore&drwNo={round}&pageGubun=L645"

# 최신 회차 추정용 (공식 JSON API)
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# 집계 범위 (최근 N회)
RANGE = 40

SIDO_LIST = [
    "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산",
    "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]

SIDO_RE = re.compile(r"^(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)\b")


def ensure_dirs():
    os.makedirs("data", exist_ok=True)


def now_kst_iso():
    # KST 고정(+09:00)
    kst = timezone(timedelta(hours=9))
    return datetime.now(tz=kst).isoformat(timespec="seconds")


def load_existing():
    if not os.path.exists(OUT):
        return None
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_json(obj):
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def fetch_json(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_html(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    # 인코딩이 이상하면 requests가 추정한 값 대신 apparent_encoding 사용
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding
    return r.text, r.status_code


def is_success_round(d: dict) -> bool:
    return (d.get("returnValue") == "success") and isinstance(d.get("drwNo"), int) and d.get("drwNo", 0) > 0


def get_latest_round_guess(max_tries: int = 40) -> int:
    """
    최신 회차를 '성공하는 drwNo'로 추정.
    OUT에 기록된 latestRound가 있으면 거기서부터 탐색.
    """
    start = 1200
    existing = load_existing()
    if existing:
        try:
            start = int(existing.get("meta", {}).get("latestRound", start))
        except Exception:
            pass

    cand = start
    for _ in range(max_tries):
        js = fetch_json(API_ROUND.format(round=cand))
        if is_success_round(js) and js.get("drwNo") == cand:
            # 다음 회차가 성공하면 계속 전진
            js2 = fetch_json(API_ROUND.format(round=cand + 1))
            if is_success_round(js2):
                cand += 1
                continue
            return cand

        # 실패면 뒤로
        cand -= 1
        time.sleep(0.15)

    raise RuntimeError("latestRound를 찾지 못했습니다. start 값을 조정해 주세요.")


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def detect_sido(addr: str):
    """
    주소 문자열에서 시/도 추출.
    """
    if not addr:
        return None

    a = normalize_text(addr)
    m = SIDO_RE.match(a)
    if m:
        return m.group(1)
    return None


def extract_address_from_row_cells(cells):
    """
    한 row의 모든 셀을 훑어서 주소처럼 보이는 셀을 찾는다.
    - '서울 ...', '경기 ...' 로 시작하면 바로 채택
    - 없으면, row 전체에서 정규식으로 주소 시작점을 찾는다
    """
    texts = [normalize_text(c) for c in cells if normalize_text(c)]
    # 1) 셀 단위로 주소 후보 찾기
    for t in texts:
        if detect_sido(t):
            return t

    # 2) row 전체 join 후 정규식 검색
    joined = " | ".join(texts)
    m = re.search(r"(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)\s+[^|]+", joined)
    if m:
        return normalize_text(m.group(0))

    return None


def parse_rank_tables(soup: BeautifulSoup):
    """
    페이지 내 table 기반 데이터 파싱.
    반환: (rank1_rows, rank2_rows)
    rankX_rows = list[list[str]] 형태로 row cells 텍스트
    """
    tables = soup.find_all("table")
    candidate_tables = []

    for tb in tables:
        # row가 실제로 존재하는 테이블만
        trs = tb.find_all("tr")
        if len(trs) < 2:
            continue

        # 헤더/캡션/주변 텍스트에 '배출' '1등' '2등' 단서가 있으면 가점
        context = normalize_text(tb.get_text(" ", strip=True))
        score = 0
        if "소재지" in context:
            score += 2
        if "배출" in context:
            score += 1
        if "1등" in context:
            score += 1
        if "2등" in context:
            score += 1
        candidate_tables.append((score, tb))

    # 점수 높은 순 정렬
    candidate_tables.sort(key=lambda x: x[0], reverse=True)

    # 보통 1등/2등 테이블이 상위에 2개 잡힘
    top = [tb for _, tb in candidate_tables[:4]]  # 넉넉히
    # 테이블을 순서대로 훑으며 row를 뽑되, 주소가 1건 이상 잡히는 테이블만 채택
    parsed = []
    for tb in top:
        rows = []
        for tr in tb.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if not tds:
                continue
            cells = [normalize_text(td.get_text(" ", strip=True)) for td in tds]
            # 헤더 row 스킵(‘번호’, ‘상호명’, ‘소재지’ 등)
            headerish = " ".join(cells)
            if ("상호" in headerish and "소재지" in headerish) or ("번호" in headerish and "소재지" in headerish):
                continue
            rows.append(cells)

        # 주소가 실제로 잡히는지 간단 검사
        addr_hits = 0
        for cells in rows[:10]:
            if detect_sido(extract_address_from_row_cells(cells) or ""):
                addr_hits += 1
        if addr_hits >= 1:
            parsed.append(rows)

    if len(parsed) >= 2:
        return parsed[0], parsed[1]
    if len(parsed) == 1:
        return parsed[0], []
    return [], []


def parse_region_counts(html: str, round_no: int):
    soup = BeautifulSoup(html, "lxml")

    rank1_rows, rank2_rows = parse_rank_tables(soup)

    def tally(rows):
        by_sido = {s: 0 for s in SIDO_LIST}
        internet = 0
        other = 0
        total = 0
        sample_addrs = []

        for cells in rows:
            total += 1
            addr = extract_address_from_row_cells(cells)

            # 인터넷 판매(주소가 아예 없거나 '동행복권' 류)
            joined = normalize_text(" ".join(cells))
            if "인터넷" in joined or "동행복권" in joined:
                internet += 1
                continue

            sido = detect_sido(addr or "")
            if sido:
                by_sido[sido] += 1
                if len(sample_addrs) < 3:
                    sample_addrs.append(addr)
            else:
                other += 1

        return {
            "totalStores": total,
            "bySido": by_sido,
            "internet": internet,
            "other": other,
            "_sampleAddrs": sample_addrs,  # 디버깅용(원하면 제거 가능)
        }

    r1 = tally(rank1_rows)
    r2 = tally(rank2_rows)

    # 로그(원인 추적용)
    print(f"[region] round={round_no} rank1_rows={len(rank1_rows)} rank2_rows={len(rank2_rows)}")
    print(f"[region] round={round_no} rank1 sample={r1.get('_sampleAddrs')}")
    print(f"[region] round={round_no} rank2 sample={r2.get('_sampleAddrs')}")

    # 디버깅 샘플은 저장본에서는 빼고 싶으면 여기서 pop 처리
    r1.pop("_sampleAddrs", None)
    r2.pop("_sampleAddrs", None)

    return {"rank1": r1, "rank2": r2}


def main():
    ensure_dirs()

    latest = get_latest_round_guess()
    start_round = max(1, latest - (RANGE - 1))

    # 기존 데이터 이어쓰기
    existing = load_existing() or {}
    rounds_obj = existing.get("rounds", {}) if isinstance(existing.get("rounds"), dict) else {}

    print(f"[region] latestRound={latest} range={RANGE} start={start_round}")

    for rnd in range(start_round, latest + 1):
        try:
            url = STORE_URL.format(round=rnd)
            html, status = fetch_html(url, timeout=25)

            print(f"[region] fetch round={rnd} status={status} len={len(html)} url={url}")

            regions = parse_region_counts(html, rnd)

            rounds_obj[str(rnd)] = regions
            time.sleep(0.35)  # 서버 배려

        except Exception as e:
            print(f"[region] ERROR round={rnd}: {e}")
            # 실패 시라도 빈 구조를 넣어 앱이 죽지 않게
            rounds_obj[str(rnd)] = {
                "rank1": {"totalStores": 0, "bySido": {s: 0 for s in SIDO_LIST}, "internet": 0, "other": 0},
                "rank2": {"totalStores": 0, "bySido": {s: 0 for s in SIDO_LIST}, "internet": 0, "other": 0},
            }
            time.sleep(0.35)

    out = {
        "meta": {
            "latestRound": latest,
            "range": RANGE,
            "updatedAt": now_kst_iso(),
        },
        "rounds": rounds_obj,
    }

    save_json(out)
    print(f"[region] wrote {OUT} rounds={len(rounds_obj)}")


if __name__ == "__main__":
    main()
