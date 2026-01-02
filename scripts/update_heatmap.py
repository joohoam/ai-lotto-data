import json
import os
import datetime
import cloudscraper

OUT = "data/heatmap.json"
API = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"
RANGE = 40

# [기준일] 1152회차 = 2024년 12월 28일
ANCHOR_ROUND = 1152
ANCHOR_DATE = datetime.datetime(2024, 12, 28, 20, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def now_kst_iso():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")

def get_latest_round_by_date() -> int:
    """날짜 기반 회차 계산 (서버 접속 X -> 차단 방지)"""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    weeks = (now - ANCHOR_DATE).days // 7
    curr = ANCHOR_ROUND + weeks
    # 토요일 21시 이전이면 아직 추첨 전
    if now.weekday() == 5 and now.hour < 21:
        curr -= 1
    return curr

def fetch_round(scraper, rnd: int) -> dict:
    # cloudscraper로 데이터 수집
    try:
        r = scraper.get(API.format(round=rnd), timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WARN] Fetch failed for {rnd}: {e}")
        return {"returnValue": "fail"}

def main():
    ensure_dirs()
    scraper = cloudscraper.create_scraper()

    # 1. 최신 회차 계산 (API 사용 안 함)
    latest = get_latest_round_by_date()
    print(f"[INFO] Calculated Latest Round: {latest}")

    # 2. 로컬 데이터 확인
    counts = {str(i): 0 for i in range(1, 46)}
    
    # 3. 데이터 수집
    start_round = max(1, latest - RANGE + 1)
    for rnd in range(start_round, latest + 1):
        js = fetch_round(scraper, rnd)
        if js.get("returnValue") != "success":
            print(f"[SKIP] Round {rnd} data missing or failed.")
            continue
            
        # 번호 카운팅
        for k in [f"drwtNo{i}" for i in range(1, 7)]:
            val = js.get(k)
            if isinstance(val, int) and 1 <= val <= 45:
                counts[str(val)] += 1

    out = {
        "meta": {
            "latestRound": latest,
            "range": RANGE,
            "updatedAt": now_kst_iso(),
        },
        "counts": counts,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
