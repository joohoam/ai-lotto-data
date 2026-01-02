import json
import os
import re
from datetime import datetime, timezone, timedelta
import cloudscraper
from bs4 import BeautifulSoup

OUT = "data/prize_2to5.json"
BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin&drwNo={round}"
KEEP_MAX = 200

def ensure_dirs(): os.makedirs("data", exist_ok=True)
def to_int(v): return int(re.sub(r"[^0-9]", "", str(v))) if v else 0

def guess_latest(scraper, start):
    cand = max(1, start)
    # 간단히 API 확인
    api_url = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={}"
    for _ in range(30):
        try:
            if scraper.get(api_url.format(cand)).json().get("returnValue") == "success": break
        except: pass
        cand -= 1
    
    latest = max(1, cand)
    for _ in range(60):
        try:
            if scraper.get(api_url.format(latest+1)).json().get("returnValue") == "success": latest += 1; continue
        except: pass
        break
    return latest

def parse_prize(html):
    soup = BeautifulSoup(html, "lxml")
    try:
        rows = soup.select("table.tbl_data tbody tr") or soup.select("table tbody tr")
    except: return {}
    
    res = {}
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds: continue
        rk = re.search(r"([2-5])", tds[0])
        if rk:
            rank = rk.group(1)
            res[rank] = {
                "totalPrize": to_int(tds[1]),
                "winners": to_int(tds[2]),
                "perGamePrize": to_int(tds[3]),
                "criteria": tds[4] if len(tds)>4 else ""
            }
    return res

def main():
    ensure_dirs()
    scraper = cloudscraper.create_scraper()
    start = 1200
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                start = int(json.load(f).get("meta", {}).get("latestRound", start))
        except: pass

    latest = guess_latest(scraper, start)
    try:
        html = scraper.get(BYWIN_URL.format(round=latest)).text
        parsed = parse_prize(html)
    except: parsed = {}

    rounds = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f: rounds = json.load(f).get("rounds", {})
        except: pass
    
    if parsed: rounds[str(latest)] = parsed
    
    # Prune
    keys = sorted([int(k) for k in rounds.keys() if str(k).isdigit()], reverse=True)[:KEEP_MAX]
    rounds = {str(k): rounds[str(k)] for k in keys}

    out = {
        "meta": {"latestRound": latest, "range": KEEP_MAX, "updatedAt": datetime.now(timezone(timedelta(hours=9))).isoformat()},
        "rounds": rounds
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
