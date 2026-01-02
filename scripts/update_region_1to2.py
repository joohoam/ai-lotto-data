import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
import cloudscraper
from bs4 import BeautifulSoup

OUT = "data/region_1to2.json"
POST_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645"
API = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={}"
RANGE = int(os.getenv("REGION_RANGE", "10"))

def get_latest(scraper):
    start = 1200
    if os.path.exists(OUT):
        try: start = int(json.load(open(OUT)).get("meta", {}).get("latestRound", 1200))
        except: pass
    cand = start
    for _ in range(100):
        try:
            if scraper.get(API.format(cand)).json().get("returnValue") == "success":
                if scraper.get(API.format(cand+1)).json().get("returnValue") == "success": cand+=1; continue
                return cand
        except: pass
        cand -= 1
    return start

def fetch_rows(scraper, rnd, rank):
    # 1등
    if rank == 1:
        d = {"method":"topStore", "nowPage":"1", "rankNo":"1", "gameNo":"5133", "drwNo":str(rnd), "schKey":"all", "schVal":""}
        try:
            soup = BeautifulSoup(scraper.post(POST_URL, data=d).text, "html.parser")
            return [ [td.text.strip() for td in tr.select("td")] for tr in soup.select("table tbody tr") if len(tr.select("td")) > 3 ]
        except: return []
    
    # 2등 (다페이지)
    rows = []
    for p in range(1, 100):
        d = {"method":"topStore", "nowPage":str(p), "rankNo":"2", "gameNo":"5133", "drwNo":str(rnd), "schKey":"all", "schVal":""}
        try:
            soup = BeautifulSoup(scraper.post(POST_URL, data=d).text, "html.parser")
            trs = soup.select("table tbody tr")
            if not trs or "조회 결과가 없습니다" in trs[0].text: break
            
            new_rows = 0
            for tr in trs:
                tds = [td.text.strip() for td in tr.select("td")]
                if len(tds) < 3: continue
                rows.append(tds)
                new_rows += 1
            if new_rows == 0: break
        except: break
        time.sleep(0.1)
    return rows

def tally(rows):
    sido = ["서울","경기","부산","대구","인천","광주","대전","울산","세종","강원","충북","충남","전북","전남","경북","경남","제주"]
    res = {s:0 for s in sido}
    internet, other, total = 0, 0, 0
    for r in rows:
        total += 1
        full = " ".join(r)
        if "인터넷" in full or "dhlottery" in full: internet += 1; continue
        
        found = False
        for s in sido:
            if s in full: res[s]+=1; found=True; break
        if not found: other += 1
    return {"totalStores":total, "bySido":res, "internet":internet, "other":other}

def main():
    os.makedirs("data", exist_ok=True)
    scraper = cloudscraper.create_scraper()
    latest = get_latest(scraper)
    start = max(1, latest - RANGE + 1)
    
    rounds = {}
    for r in range(start, latest+1):
        try:
            r1 = fetch_rows(scraper, r, 1)
            r2 = fetch_rows(scraper, r, 2)
            rounds[str(r)] = {"rank1": tally(r1), "rank2": tally(r2)}
        except: pass
        time.sleep(0.1)
        
    out = {
        "meta": {"latestRound": latest, "range": RANGE, "updatedAt": datetime.now(timezone(timedelta(hours=9))).isoformat()},
        "rounds": {k: rounds[k] for k in sorted(rounds.keys(), key=int, reverse=True)}
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
