import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import cloudscraper
from bs4 import BeautifulSoup
from bs4.element import Tag

OUT = "data/region_1to2.json"
POST_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645"
API_ROUND = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

RANGE = int(os.getenv("REGION_RANGE", "10"))
MAX_PAGES = int(os.getenv("REGION_MAX_PAGES", "220"))
SIDO_LIST = ["서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
SIDO_RE = re.compile(r"^(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)\b")
RANK_LABEL_RE = re.compile(r"([12])\s*등\s*배출점")
ONLINE_KEYWORDS = ["인터넷 복권판매사이트", "dhlottery.co.kr"]

def ensure_dirs(): os.makedirs("data", exist_ok=True)
def now_kst_iso(): return datetime.now(tz=timezone(timedelta(hours=9))).isoformat(timespec="seconds")
def normalize_text(s): return re.sub(r"\s+", " ", (s or "").strip())

def fetch_json(scraper, url):
    r = scraper.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def get_latest_round_guess(scraper):
    start = 1200
    if os.path.exists(OUT):
        try:
            with open(OUT, "r") as f: start = int(json.load(f).get("meta", {}).get("latestRound", 1200))
        except: pass
    
    cand = start
    for _ in range(150):
        try:
            js = fetch_json(scraper, API_ROUND.format(round=cand))
            if js.get("returnValue") == "success" and js.get("drwNo") == cand:
                try:
                    js2 = fetch_json(scraper, API_ROUND.format(round=cand+1))
                    if js2.get("returnValue") == "success": cand += 1; time.sleep(0.1); continue
                except: pass
                return cand
        except: pass
        cand -= 1
        time.sleep(0.1)
    raise RuntimeError("latestRound failed")

def fetch_rank_page(scraper, round_no, rank_no, page):
    data = {
        "method": "topStore", "nowPage": str(page), "rankNo": str(rank_no),
        "rank": str(rank_no), "gameNo": "5133", "drwNo": str(round_no), "schKey": "all", "schVal": ""
    }
    r = scraper.post(POST_URL, data=data, timeout=30)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"): r.encoding = r.apparent_encoding
    return BeautifulSoup(r.text, "html.parser")

def find_rank_table(soup, rank_no):
    best, best_score = None, -1.0
    for tb in soup.find_all("table"):
        txt = normalize_text(tb.get_text(" ", strip=True))
        score = 0.0
        if "상호" in txt: score += 2.0
        if "소재지" in txt or "주소" in txt: score += 2.0
        if score > best_score: best_score = score; best = tb
    return best

def parse_rows(tb, rank_no):
    rows = []
    other = 2 if int(rank_no)==1 else 1
    stop_re = re.compile(rf"{other}\s*등\s*배출점")
    for tr in tb.find_all("tr"):
        txt = normalize_text(tr.get_text(" "))
        if stop_re.search(txt): break
        tds = [normalize_text(td.get_text(" ")) for td in tr.find_all(["td","th"])]
        if len(tds)>=3 and "상호" not in tds[0]: rows.append(tds)
    return rows

def fetch_rank_rows(scraper, round_no, rank_no):
    if int(rank_no) == 1:
        soup = fetch_rank_page(scraper, round_no, 1, 1)
        tb = find_rank_table(soup, 1)
        return parse_rows(tb, 1) if tb else []
    
    all_rows = []
    seen = set()
    for page in range(1, MAX_PAGES+1):
        soup = fetch_rank_page(scraper, round_no, 2, page)
        tb = find_rank_table(soup, 2)
        if not tb: break
        rows = parse_rows(tb, 2)
        added = 0
        for r in rows:
            k = "|".join(r)
            if k not in seen: seen.add(k); all_rows.append(r); added += 1
        if added == 0: break
        time.sleep(0.1)
    return all_rows

def tally(rows):
    by_sido = {s: 0 for s in SIDO_LIST}
    internet, other, total = 0, 0, 0
    for cells in rows:
        total += 1
        joined = " ".join(cells)
        if any(k in joined for k in ONLINE_KEYWORDS): internet += 1; continue
        found = False
        for c in cells:
            m = SIDO_RE.match(normalize_text(c))
            if m: by_sido[m.group(1)] += 1; found = True; break
        if not found: other += 1
    return {"totalStores": total, "bySido": by_sido, "internet": internet, "other": other}

def main():
    ensure_dirs()
    scraper = cloudscraper.create_scraper()
    latest = get_latest_round_guess(scraper)
    start = max(1, latest - RANGE + 1)
    rounds_obj = {}
    
    for rnd in range(start, latest+1):
        try:
            r1 = fetch_rank_rows(scraper, rnd, 1)
            r2 = fetch_rank_rows(scraper, rnd, 2)
            rounds_obj[str(rnd)] = {"rank1": tally(r1), "rank2": tally(r2)}
        except Exception as e:
            print(f"Error {rnd}: {e}")
        time.sleep(0.1)

    out = {
        "meta": { "latestRound": latest, "range": RANGE, "updatedAt": now_kst_iso() },
        "rounds": {k: rounds_obj[k] for k in sorted(rounds_obj.keys(), key=int, reverse=True)}
    }
    with open(OUT, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
