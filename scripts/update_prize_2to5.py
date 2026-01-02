import json
import os
import re
from datetime import datetime, timezone, timedelta
import cloudscraper
from bs4 import BeautifulSoup

OUT = "data/prize_2to5.json"
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"
BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin&drwNo={round}"
KEEP_MAX = 200
KST = timezone(timedelta(hours=9))

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def to_int(v) -> int:
    if v is None: return 0
    if isinstance(v, (int, float)): return int(v)
    s = str(v)
    digits = re.sub(r"[^0-9]", "", s)
    return int(digits) if digits else 0

def fetch_round_exists(scraper, rnd: int) -> bool:
    r = scraper.get(API_ROUND.format(round=rnd), timeout=20)
    r.raise_for_status()
    j = r.json()
    return j.get("returnValue") == "success" and j.get("drwNo") == rnd

def guess_latest_round(scraper, start: int = 1200, max_step: int = 60) -> int:
    cand = max(1, start)
    for _ in range(30):
        try:
            if fetch_round_exists(scraper, cand): break
        except: pass
        cand -= 1
        if cand <= 1: cand = 1; break

    latest = cand
    for _ in range(max_step):
        nxt = latest + 1
        try:
            if fetch_round_exists(scraper, nxt):
                latest = nxt
                continue
        except: pass
        break
    return latest

def parse_prize_2to5(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.tbl_data") or soup.select_one("table.tbl_data_col") or soup.select_one("table")
    if table is None: raise RuntimeError("Table not found")
    rows = table.select("tbody tr") or table.select("tr")
    result = {}
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds: continue
        m = re.search(r"([2-5])\s*등", tds[0])
        rank = m.group(1) if m else (re.fullmatch(r"[2-5]", tds[0]).group(0) if re.fullmatch(r"[2-5]", tds[0]) else None)
        if not rank: continue
        
        result[rank] = {
            "totalPrize": to_int(tds[1]) if len(tds)>1 else 0,
            "winners": to_int(tds[2]) if len(tds)>2 else 0,
            "perGamePrize": to_int(tds[3]) if len(tds)>3 else 0,
            "criteria": tds[4] if len(tds)>4 else None,
        }
    return result

def load_existing() -> dict:
    if not os.path.exists(OUT): return {"meta": {}, "rounds": {}}
    try:
        with open(OUT, "r", encoding="utf-8") as f: j = json.load(f)
        if isinstance(j, dict) and "rounds" in j: return j
    except: pass
    return {"meta": {}, "rounds": {}}

def prune_rounds(rounds: dict) -> dict:
    keys = sorted([int(k) for k in rounds.keys() if str(k).isdigit()], reverse=True)
    keep = set(keys[:KEEP_MAX])
    return {str(k): rounds[str(k)] for k in keep if str(k) in rounds}

def main():
    ensure_dirs()
    scraper = cloudscraper.create_scraper()
    
    existing = load_existing()
    rounds = existing.get("rounds", {})
    start = 1200
    meta_latest = to_int(existing.get("meta", {}).get("latestRound"))
    if meta_latest > 0: start = meta_latest

    latest = guess_latest_round(scraper, start=start)

    url = BYWIN_URL.format(round=latest)
    r = scraper.get(url, timeout=30)
    r.raise_for_status()

    parsed = parse_prize_2to5(r.text)
    rounds[str(latest)] = parsed
    rounds = prune_rounds(rounds)

    out = {
        "meta": { "latestRound": latest, "range": KEEP_MAX, "updatedAt": datetime.now(KST).isoformat() },
        "rounds": rounds,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
