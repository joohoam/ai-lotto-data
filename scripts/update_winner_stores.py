import json
import os
import re
import time
from datetime import datetime, timezone
import cloudscraper
from bs4 import BeautifulSoup

OUT = "data/winner_stores.json"
TOPSTORE_URL = "https://dhlottery.co.kr/store.do"
API = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={}"
RANGE = int(os.getenv("WINNER_STORES_RANGE", "10"))

def get_latest(scraper):
    start = 1200
    if os.path.exists(OUT):
        try: start = int(json.load(open(OUT)).get("meta", {}).get("latestRound", 1200))
        except: pass
    cand = start
    for _ in range(50):
        try:
            if scraper.get(API.format(cand)).json().get("returnValue") == "success":
                if scraper.get(API.format(cand+1)).json().get("returnValue") == "success": cand+=1; continue
                return cand
        except: pass
        cand -= 1
    return start

def crawl_round(scraper, rnd):
    rows = []
    # Rank 1
    try:
        d = {"method":"topStore", "nowPage":"1", "rankNo":"1", "gameNo":"5133", "drwNo":str(rnd), "schKey":"all", "schVal":""}
        soup = BeautifulSoup(scraper.post(TOPSTORE_URL, data=d).text, "html.parser")
        for tr in soup.select("table tbody tr"):
            tds = [td.text.strip() for td in tr.select("td")]
            if len(tds) > 3 and "조회 결과가 없습니다" not in tds[0]:
                rows.append({"round":rnd, "rank":1, "storeName":tds[1], "method":tds[2], "address":tds[3]})
    except: pass
    
    # Rank 2
    for p in range(1, 100):
        try:
            d = {"method":"topStore", "nowPage":str(p), "rankNo":"2", "gameNo":"5133", "drwNo":str(rnd), "schKey":"all", "schVal":""}
            soup = BeautifulSoup(scraper.post(TOPSTORE_URL, data=d).text, "html.parser")
            trs = soup.select("table tbody tr")
            if not trs or "조회 결과가 없습니다" in trs[0].text: break
            
            added = 0
            for tr in trs:
                tds = [td.text.strip() for td in tr.select("td")]
                if len(tds) > 2:
                    rows.append({"round":rnd, "rank":2, "storeName":tds[1], "address":tds[2]})
                    added += 1
            if added == 0: break
        except: break
        time.sleep(0.1)
    return rows

def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    scraper = cloudscraper.create_scraper()
    latest = get_latest(scraper)
    start = max(1, latest - RANGE + 1)
    
    all_rows = []
    for r in range(start, latest+1):
        all_rows.extend(crawl_round(scraper, r))
        time.sleep(0.2)
        
    # Aggregate
    # (간소화 로직)
    by_round = {}
    for row in all_rows:
        by_round.setdefault(str(row["round"]), []).append(row)
        
    out = {
        "meta": {"latestRound": latest, "range": RANGE, "updatedAt": datetime.now(timezone.utc).isoformat()},
        "byRound": by_round
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
