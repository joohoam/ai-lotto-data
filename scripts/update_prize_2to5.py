import json
import os
import re
from datetime import datetime, timezone, timedelta
import cloudscraper
import requests
from bs4 import BeautifulSoup

OUT = "data/prize_2to5.json"
BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin&drwNo={round}"
NAVER_URL = "https://search.naver.com/search.naver?where=nexearch&query={round}회로또"
KEEP_MAX = 200

# 기준일: 1152회 = 2024년 12월 28일
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

def parse_prize_official(html):
    """동행복권 사이트 파싱"""
    soup = BeautifulSoup(html, "lxml")
    try:
        rows = soup.select("table.tbl_data tbody tr") or soup.select("table tbody tr")
    except: return {}
    
    res = {}
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds: continue
        rk_match = re.search(r"([2-5])", tds[0])
        if rk_match:
            rank = rk_match.group(1)
            res[rank] = {
                "totalPrize": to_int(tds[1]),
                "winners": to_int(tds[2]),
                "perGamePrize": to_int(tds[3]),
                "criteria": tds[4] if len(tds) > 4 else ""
            }
    return res

def parse_prize_naver(html):
    """네이버 검색 결과 파싱 (동행복권 차단 시 사용)"""
    soup = BeautifulSoup(html, "lxml")
    res = {}
    try:
        # 네이버 등수별 당첨금 테이블 (class="win_amount")
        # 구조: 등수 | 당첨금액 | 당첨게임수
        # 주의: 네이버는 1등부터 5등까지 순서대로 나옴
        rows = soup.select(".win_amount tbody tr")
        for tr in rows:
            tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
            if len(tds) < 3: continue
            
            rank_txt = tds[0] # 예: "1등", "2등"
            rk_match = re.search(r"([2-5])", rank_txt) # 2~5등만 추출
            if not rk_match: continue
            
            rank = rk_match.group(1)
            # 네이버 컬럼: [등수] [총당첨금(없을수도있음)] [1인당당첨금] [당첨자수] ... 구조가 검색시점에 따라 다를 수 있음
            # 보통: 등수 | 1인당 당첨금 | 당첨게임수 | 비고
            
            per_game = to_int(tds[1])
            winners = to_int(tds[2])
            total_prize = per_game * winners # 총액 역산
            
            res[rank] = {
                "totalPrize": total_prize,
                "winners": winners,
                "perGamePrize": per_game,
                "criteria": "당첨금 기준" # 네이버엔 기준 텍스트가 명확치 않아 임의값
            }
    except Exception as e:
        print(f"[WARN] Naver parsing error: {e}")
        
    return res

def fetch_data(scraper, rnd):
    # 1. 동행복권 시도
    try:
        print(f"[INFO] Trying Official for {rnd}...")
        url = BYWIN_URL.format(round=rnd)
        resp = scraper.get(url, timeout=10)
        if resp.status_code == 200 and "rsaModulus" not in resp.text:
            data = parse_prize_official(resp.text)
            if data: return data
    except Exception as e:
        print(f"[WARN] Official failed: {e}")

    # 2. 네이버 시도
    try:
        print(f"[INFO] Trying Naver fallback for {rnd}...")
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(NAVER_URL.format(round=rnd), headers=headers, timeout=10)
        if resp.status_code == 200:
            data = parse_prize_naver(resp.text)
            if data: 
                print(f"[INFO] Naver fetch success for {rnd}")
                return data
    except Exception as e:
        print(f"[WARN] Naver failed: {e}")
        
    return {}

def main():
    ensure_dirs()
    scraper = cloudscraper.create_scraper()
    
    # 1. 최신 회차 계산
    latest = get_latest_round_by_date()
    print(f"[INFO] Target Latest Round: {latest}")

    # 2. 데이터 수집
    parsed = fetch_data(scraper, latest)

    # 3. 기존 데이터 병합 및 저장
    rounds = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                rounds = json.load(f).get("rounds", {})
        except: pass
    
    if parsed:
        rounds[str(latest)] = parsed
    else:
        print(f"[ERROR] Failed to fetch prize data for {latest}")

    # 데이터 정리
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
