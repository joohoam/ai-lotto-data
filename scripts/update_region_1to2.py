import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
import cloudscraper
from bs4 import BeautifulSoup

OUT = "data/region_1to2.json"
POST_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645"
RANGE = int(os.getenv("REGION_RANGE", "10"))

ANCHOR_ROUND = 1152
ANCHOR_DATE = datetime(2024, 12, 28, 20, 0, 0, tzinfo=timezone(timedelta(hours=9)))

def ensure_dirs(): os.makedirs("data", exist_ok=True)
def normalize_text(s): return re.sub(r"\s+", " ", (s or "").strip())

def get_latest_round_by_date() -> int:
    now = datetime.now(timezone(timedelta(hours=9)))
    weeks = (now - ANCHOR_DATE).days // 7
    curr = ANCHOR_ROUND + weeks
    if now.weekday() == 5 and now.hour < 21:
        curr -= 1
    return curr

def fetch_rank_rows(scraper, rnd, rank):
    # 1등 (페이지 없음)
    if rank == 1:
        data = {"method":"topStore", "nowPage":"1", "rankNo":"1", "gameNo":"5133", "drwNo":str(rnd), "schKey":"all", "schVal":""}
        try:
            soup = BeautifulSoup(scraper.post(POST_URL, data=data, timeout=30).text, "html.parser")
            # 테이블 파싱
            rows = []
            for tr in soup.select("table tbody tr"):
                tds = [td.text.strip() for td in tr.select("td")]
                if len(tds) >= 3 and "조회 결과가 없습니다" not in tds[0]:
                    rows.append(tds)
            return rows
        except: return []

    # 2등 (페이지네이션)
    rows = []
    for page in range(1, 150): # 최대 150페이지
        data = {"method":"topStore", "nowPage":str(page), "rankNo":"2", "gameNo":"5133", "drwNo":str(rnd), "schKey":"all", "schVal":""}
        try:
            soup = BeautifulSoup(scraper.post(POST_URL, data=data, timeout=30).text, "html.parser")
            trs = soup.select("table tbody tr")
            if not trs: break
            
            # 데이터 없음 확인
            if "조회 결과가 없습니다" in trs[0].text: break
            
            added = 0
            for tr in trs:
                tds = [td.text.strip() for td in tr.select("td")]
                if len(tds) >= 3:
                    rows.append(tds)
                    added += 1
            if added == 0: break
        except: break
        time.sleep(0.1)
    return rows

def tally(rows):
    sido_list = ["서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
    res = {s: 0 for s in sido_list}
    internet, other, total = 0, 0, 0
    
    for r in rows:
        total += 1
        full = " ".join(r)
        if "인터넷" in full or "dhlottery" in full:
            internet += 1
            continue
        
        found = False
        for s in sido_list:
            if s in full:
                res[s] += 1
                found = True
                break
        if not found: other += 1
            
    return {"totalStores": total, "bySido": res, "internet": internet, "other": other}

def main():
    ensure_dirs()
    scraper = cloudscraper.create_scraper()
    latest = get_latest_round_by_date()
    print(f"[INFO] Latest Round: {latest}")

    rounds_obj = {}
    start = max(1, latest - RANGE + 1)
    
    for rnd in range(start, latest + 1):
        try:
            r1 = fetch_rank_rows(scraper, rnd, 1)
            r2 = fetch_rank_rows(scraper, rnd, 2)
            rounds_obj[str(rnd)] = {"rank1": tally(r1), "rank2": tally(r2)}
        except Exception as e:
            print(f"[WARN] Failed region fetch for {rnd}: {e}")
        time.sleep(0.1)

    # 저장
    keys = sorted(rounds_obj.keys(), key=int, reverse=True)
    out = {
        "meta": {"latestRound": latest, "range": RANGE, "updatedAt": datetime.now(timezone(timedelta(hours=9))).isoformat()},
        "rounds": {k: rounds_obj[k] for k in keys}
    }
    
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
