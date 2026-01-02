import json
import os
import datetime
import cloudscraper

OUT = "data/heatmap.json"
API = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"
RANGE = 40

def ensure_dirs(): os.makedirs("data", exist_ok=True)
def now_kst_iso(): return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")

def fetch_round(scraper, rnd: int) -> dict:
    r = scraper.get(API.format(round=rnd), timeout=30)
    r.raise_for_status()
    return r.json()

def guess_latest(scraper, start=1200):
    cand = start
    for _ in range(80):
        try:
            if fetch_round(scraper, cand).get("returnValue") == "success":
                try:
                    if fetch_round(scraper, cand+1).get("returnValue") == "success":
                        cand += 1; continue
                except: pass
                return cand
        except: pass
        cand -= 1
    raise RuntimeError("latestRound failed")

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
    counts = {str(i): 0 for i in range(1, 46)}
    
    for rnd in range(max(1, latest - RANGE + 1), latest + 1):
        try:
            js = fetch_round(scraper, rnd)
            if js.get("returnValue") != "success": continue
            for k in [f"drwtNo{i}" for i in range(1,7)]:
                n = js.get(k)
                if isinstance(n, int): counts[str(n)] += 1
        except: pass

    out = {
        "meta": {"latestRound": latest, "range": RANGE, "updatedAt": now_kst_iso()},
        "counts": counts
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
