import json
import os
import re
import time
import datetime
import requests
from bs4 import BeautifulSoup

OUT = "data/prize_2to5.json"
BASE = "https://dhlottery.co.kr/gameResult.do?method=byWin"
URL = "https://dhlottery.co.kr/gameResult.do?method=byWin&drwNo={round}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

RANGE = 40  # ✅ 최신 40회만 갱신

API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def now_kst_iso():
    kst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(kst).isoformat(timespec="seconds")

def to_int(s: str) -> int:
    digits = re.sub(r"[^\d]", "", s or "")
    return int(digits) if digits else 0

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

def parse_2to5(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    # 당첨금 테이블(2~5등 포함)
    rows = soup.select("table.tbl_data tbody tr") or soup.select("table tbody tr")

    data = {}
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds:
            continue

        # 등위: "2등", "3등", "4등", "5등"
        if tds[0] in ["2등", "3등", "4등", "5등"]:
            rank = int(tds[0].replace("등", ""))
            total = to_int(tds[1]) if len(tds) > 1 else 0
            winners = to_int(tds[2]) if len(tds) > 2 else 0
            per_game = to_int(tds[3]) if len(tds) > 3 else 0
            criteria = tds[4] if len(tds) > 4 else None

            data[str(rank)] = {
                "totalPrize": total,
                "winners": winners,
                "perGamePrize": per_game,
                "criteria": criteria,
            }

    # 2~5등이 하나도 안 잡히면 실패로 처리(기존 캐시 유지용)
    if not any(k in data for k in ["2", "3", "4", "5"]):
        raise RuntimeError("2~5등 테이블 파싱 실패")
    return data

def main():
    ensure_dirs()
    existing = load_existing()

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

    # 최신 RANGE 범위만 업데이트(기존 전체 데이터는 유지)
    for rnd in range(start_round, latest + 1):
        key = str(rnd)
        need = True
        if key in rounds and isinstance(rounds[key], dict):
            # 2~5등이 모두 있으면 스킵
            if all(str(r) in rounds[key] for r in [2, 3, 4, 5]):
                need = False
        if not need:
            continue

        r = requests.get(URL.format(round=rnd), headers=HEADERS, timeout=20)
        r.raise_for_status()
        if r.encoding is None or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"

        rounds[key] = parse_2to5(r.text)
        time.sleep(0.25)

    out = {
        "meta": {
            "latestRound": latest,
            "range": RANGE,
            "updatedAt": now_kst_iso(),
        },
        "rounds": {k: rounds[k] for k in sorted(rounds.keys(), key=lambda x: int(x))},
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
