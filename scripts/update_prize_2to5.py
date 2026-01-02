import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

OUT = "data/prize_2to5.json"

# 회차 존재 여부 확인용(공식 JSON API)
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

# 2~5등 당첨정보 페이지(스크래핑)
BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin&drwNo={round}"

# [수정] 봇 차단 방지용 헤더 (User-Agent 구체화 + Referer 추가)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.dhlottery.co.kr/"
}

# 파일에 누적 저장할 최대 회차 개수 (최근 N회)
KEEP_MAX = 200

KST = timezone(timedelta(hours=9))


def ensure_dirs():
    os.makedirs("data", exist_ok=True)


def to_int(v) -> int:
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v)
    digits = re.sub(r"[^0-9]", "", s)
    return int(digits) if digits else 0


def fetch_round_exists(rnd: int) -> bool:
    """해당 회차가 존재하면 True (returnValue == 'success')"""
    r = requests.get(API_ROUND.format(round=rnd), headers=HEADERS, timeout=15)
    r.raise_for_status()
    j = r.json()
    return j.get("returnValue") == "success" and j.get("drwNo") == rnd


def guess_latest_round(start: int = 1200, max_step: int = 60) -> int:
    """
    start 근처에서 앞으로 탐색하면서 최신 회차를 찾음.
    - 존재하면 앞으로 +1
    - 존재 안하면 바로 직전이 최신
    """
    cand = max(1, start)

    # start가 너무 앞이거나 뒤일 수 있으니, 먼저 start가 유효한지 보정
    # start가 너무 크면 내려가서 유효한 회차 찾기
    for _ in range(30):
        try:
            if fetch_round_exists(cand):
                break
        except Exception:
            pass
        cand -= 1
        if cand <= 1:
            cand = 1
            break

    # 이제 cand가 유효한 회차라고 가정하고 앞으로 전진
    latest = cand
    for _ in range(max_step):
        nxt = latest + 1
        try:
            if fetch_round_exists(nxt):
                latest = nxt
                continue
        except Exception:
            pass
        break

    return latest


def parse_prize_2to5(html: str) -> dict:
    """
    byWin 페이지에서 2~5등 정보를 파싱
    반환: {"2": {...}, "3": {...}, "4": {...}, "5": {...}}
    """
    soup = BeautifulSoup(html, "lxml")

    # 테이블 후보들: 사이트 구조가 바뀌면 selector가 달라질 수 있어 후보를 여러 개 둠
    table = (
        soup.select_one("table.tbl_data")
        or soup.select_one("table.tbl_data_col")
        or soup.select_one("table")
    )
    if table is None:
        raise RuntimeError("2~5등 테이블을 찾지 못했습니다.")

    rows = table.select("tbody tr") or table.select("tr")

    result = {}
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds:
            continue

        # 보통 첫 컬럼이 "2등" "3등" 같은 형태
        m = re.search(r"([2-5])\s*등", tds[0])
        if not m:
            # 혹시 "2"만 있을 경우 대비
            m2 = re.fullmatch(r"[2-5]", tds[0])
            if not m2:
                continue
            rank = m2.group(0)
        else:
            rank = m.group(1)

        # 컬럼 구성은 케이스가 조금씩 달라서 유연하게 처리
        # 기대값: 총당첨금 / 당첨게임수 / 1게임당 당첨금 / 당첨기준(문구)
        total_prize = to_int(tds[1]) if len(tds) > 1 else 0
        winners = to_int(tds[2]) if len(tds) > 2 else 0
        per_game = to_int(tds[3]) if len(tds) > 3 else 0
        criteria = tds[4] if len(tds) > 4 else None

        result[rank] = {
            "totalPrize": total_prize,
            "winners": winners,
            "perGamePrize": per_game,
            "criteria": criteria,
        }

    # 최소 2~5등 중 하나라도 나와야 정상
    if not any(k in result for k in ["2", "3", "4", "5"]):
        raise RuntimeError("2~5등 파싱 결과가 비어 있습니다(사이트 구조/셀렉터 확인 필요).")

    return result


def load_existing() -> dict:
    if not os.path.exists(OUT):
        return {"meta": {}, "rounds": {}}

    try:
        with open(OUT, "r", encoding="utf-8") as f:
            j = json.load(f)

        # 형식 호환:
        # 1) {"meta":..., "rounds": {...}} 형태
        # 2) {"1202": {...}, "1201": {...}} 형태(옛날)
        if isinstance(j, dict) and "rounds" in j and isinstance(j["rounds"], dict):
            meta = j.get("meta", {}) if isinstance(j.get("meta", {}), dict) else {}
            return {"meta": meta, "rounds": j["rounds"]}

        # flat 형태면 rounds로 감싸서 변환
        if isinstance(j, dict):
            rounds = {}
            for k, v in j.items():
                if str(k).isdigit() and isinstance(v, dict):
                    rounds[str(k)] = v
            return {"meta": {}, "rounds": rounds}

    except Exception:
        pass

    return {"meta": {}, "rounds": {}}


def prune_rounds(rounds: dict) -> dict:
    keys = [int(k) for k in rounds.keys() if str(k).isdigit()]
    keys.sort(reverse=True)
    keep = set(keys[:KEEP_MAX])
    return {str(k): rounds[str(k)] for k in keep if str(k) in rounds}


def main():
    ensure_dirs()

    existing = load_existing()
    rounds = existing.get("rounds", {})

    # start 기준: 기존 meta.latestRound가 있으면 거기서 시작, 없으면 1200
    start = 1200
    meta_latest = to_int(existing.get("meta", {}).get("latestRound"))
    if meta_latest > 0:
        start = meta_latest

    latest = guess_latest_round(start=start, max_step=80)

    # 최신 회차 byWin 페이지 가져오기
    url = BYWIN_URL.format(round=latest)
    # [수정] 헤더 적용
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()

    parsed = parse_prize_2to5(r.text)

    # rounds에 최신 회차 데이터 저장(누적)
    rounds[str(latest)] = parsed

    # 오래된 회차 정리
    rounds = prune_rounds(rounds)

    out = {
        "meta": {
            "latestRound": latest,
            "range": KEEP_MAX,
            "updatedAt": datetime.now(KST).isoformat(),
        },
        "rounds": rounds,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[OK] latestRound={latest}, savedRoundKeys(top5)={sorted([int(k) for k in rounds.keys()], reverse=True)[:5]}")
    print(f"[OK] rank2to5 keys={list(parsed.keys())}")


if __name__ == "__main__":
    main()
