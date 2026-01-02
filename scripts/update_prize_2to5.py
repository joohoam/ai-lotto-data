import json
import os
import re
from datetime import datetime, timezone, timedelta
import cloudscraper
from bs4 import BeautifulSoup

OUT = "data/prize_2to5.json"
BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin&drwNo={round}"
KEEP_MAX = 200

ANCHOR_ROUND = 1152
ANCHOR_DATE = datetime(2024, 12, 28, 20, 0, 0, tzinfo=timezone(timedelta(hours=9)))

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def to_int(v):
    if v is None: return 0
    return int(re.sub(r"[^0-9]", "", str(v))) if str(v).strip() else 0

def get_latest_round_by_date() -> int:
    now = datetime.now(timezone(timedelta(hours=9)))
    weeks = (now - ANCHOR_DATE).days // 7
    curr = ANCHOR_ROUND + weeks
    if now.weekday() == 5 and now.hour < 21:
        curr -= 1
    return curr

def parse_prize(html):
    soup = BeautifulSoup(html, "lxml")
    try:
        # 테이블 찾기 (여러 후보)
        rows = soup.select("table.tbl_data tbody tr") or soup.select("table tbody tr")
    except: return {}
    
    res = {}
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds: continue
        # 등수 파싱
        rk_match = re.search(r"([2-5])", tds[0])
        if rk_match:
            rank = rk_match.group(1)
            res[rank] = {
                "totalPrize": to_int(tds[1]) if len(tds) > 1 else 0,
                "winners": to_int(tds[2]) if len(tds) > 2 else 0,
                "perGamePrize": to_int(tds[3]) if len(tds) > 3 else 0,
                "criteria": tds[4] if len(tds) > 4 else ""
            }
    return res

def main():
    ensure_dirs()
    scraper = cloudscraper.create_scraper()
    
    # 1. 최신 회차 계산
    latest = get_latest_round_by_date()
    print(f"[INFO] Latest Round: {latest}")

    # 2. 크롤링
    parsed = {}
    try:
        url = BYWIN_URL.format(round=latest)
        html = scraper.get(url, timeout=30).text
        parsed = parse_prize(html)
    except Exception as e:
        print(f"[WARN] Failed to fetch prize data: {e}")

    # 3. 기존 데이터 병합
    rounds = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                rounds = json.load(f).get("rounds", {})
        except: pass
    
    if parsed:
        rounds[str(latest)] = parsed
    
    # 4. 데이터 정리 (최근 N개만 유지)
    valid_keys = sorted([int(k) for k in rounds.keys() if str(k).isdigit()], reverse=True)[:KEEP_MAX]
    rounds = {str(k): rounds[str(k)] for k in valid_keys}

    out = {
        "meta": {
            "latestRound": latest, 
            "range": KEEP_MAX, 
            "updatedAt": datetime.now(timezone(timedelta(hours=9))).isoformat()
        },
        "rounds": rounds
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
