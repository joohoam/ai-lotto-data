import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup

OUT = "data/prize_2to5.json"
BASE = "https://dhlottery.co.kr/gameResult.do?method=byWin"
URL = "https://dhlottery.co.kr/gameResult.do?method=byWin&drwNo={round}"

HEADERS = {"User-Agent": "Mozilla/5.0"}

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def to_int(s: str) -> int:
    digits = re.sub(r"[^\d]", "", s or "")
    return int(digits) if digits else 0

def get_latest_round() -> int:
    r = requests.get(BASE, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    # 흔히 select option에 최신 회차가 노출됨
    opt = soup.select_one("select option[selected]") or soup.select_one("select option")
    if opt and opt.get("value", "").isdigit():
        return int(opt["value"])

    # fallback: “1200회” 패턴
    m = re.search(r"(\d+)\s*회", soup.get_text(" ", strip=True))
    if m:
        return int(m.group(1))

    raise RuntimeError("최신 회차를 찾지 못했습니다.")

def parse_2to5(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("table.tbl_data tbody tr") or soup.select("table tbody tr")

    data = {}
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds:
            continue

        # 기대 형태: [등위, 총당첨금, 당첨게임 수, 1게임당 당첨금, 당첨기준]
        if tds[0] in ["2등", "3등", "4등", "5등"]:
            rank = tds[0].replace("등", "")
            total = to_int(tds[1]) if len(tds) > 1 else 0
            winners = to_int(tds[2]) if len(tds) > 2 else 0
            per = to_int(tds[3]) if len(tds) > 3 else 0
            criteria = tds[4] if len(tds) > 4 else ""
            data[rank] = {
                "totalPrize": total,
                "winners": winners,
                "perGamePrize": per,
                "criteria": criteria,
            }

    for rnk in ["2", "3", "4", "5"]:
        if rnk not in data:
            raise RuntimeError(f"{rnk}등 파싱 실패(페이지 구조 변경 가능)")
    return data

def main():
    ensure_dirs()

    existing = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                existing = json.load(f) or {}
        except Exception:
            existing = {}

    latest = get_latest_round()

    # 최근 40회만 갱신(필요시 조정)
    start = max(1, latest - 40)
    for rnd in range(start, latest + 1):
        key = str(rnd)
        if key in existing and all(k in existing[key] for k in ["2", "3", "4", "5"]):
            continue

        r = requests.get(URL.format(round=rnd), headers=HEADERS, timeout=20)
        r.raise_for_status()
        existing[key] = parse_2to5(r.text)
        time.sleep(0.25)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()

