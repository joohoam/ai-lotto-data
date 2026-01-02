import json
import os
import datetime
import re
import requests
import cloudscraper

OUT = "data/heatmap.json"
# 동행복권 API
API_URL = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"
# 네이버 검색 URL
NAVER_URL = "https://search.naver.com/search.naver?where=nexearch&query={round}회로또"

# 기준일: 1152회 = 2024년 12월 28일
ANCHOR_ROUND = 1152
ANCHOR_DATE = datetime.datetime(2024, 12, 28, 20, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def now_kst_iso():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")

def get_latest_round_by_date() -> int:
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    weeks = (now - ANCHOR_DATE).days // 7
    curr = ANCHOR_ROUND + weeks
    if now.weekday() == 5 and now.hour < 21:
        curr -= 1
    return curr

def fetch_from_naver(rnd: int) -> dict:
    """동행복권 차단 시 네이버 검색 결과 파싱"""
    print(f"[INFO] Trying Naver fallback for round {rnd}...")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        resp = requests.get(NAVER_URL.format(round=rnd), headers=headers, timeout=10)
        resp.raise_for_status()
        html = resp.text
        
        # 네이버 당첨번호 파싱 (div class="win_number_box")
        # 번호 추출 로직: <span class="ball">1</span> ...
        numbers = re.findall(r'<span class=["\']ball[^>]*>(\d+)</span>', html)
        
        # 보너스 번호 포함 총 7개여야 함
        if len(numbers) >= 6:
            # 네이버는 보너스 번호가 뒤에 따로 나옴. 
            # API 포맷(drwtNo1~6, bnusNo)에 맞춰 변환
            data = {"returnValue": "success", "drwNo": rnd}
            for i in range(6):
                data[f"drwtNo{i+1}"] = int(numbers[i])
            # 보너스 (7번째가 있다면)
            if len(numbers) >= 7:
                data["bnusNo"] = int(numbers[6])
            
            print(f"[INFO] Naver fetch success for {rnd}: {numbers[:6]}")
            return data
            
    except Exception as e:
        print(f"[WARN] Naver fetch failed: {e}")
    
    return {"returnValue": "fail"}

def fetch_round(scraper, rnd: int) -> dict:
    # 1차 시도: 동행복권 (Cloudscraper)
    try:
        r = scraper.get(API_URL.format(round=rnd), timeout=15)
        if r.status_code == 200:
            js = r.json()
            if js.get("returnValue") == "success":
                return js
    except Exception:
        pass
        
    # 2차 시도: 네이버 (Requests)
    return fetch_from_naver(rnd)

def main():
    ensure_dirs()
    scraper = cloudscraper.create_scraper()

    # 1. 최신 회차 계산
    latest = get_latest_round_by_date()
    print(f"[INFO] Target Latest Round: {latest}")

    # 2. 로컬 데이터 로드 (기존 카운트 유지)
    counts = {str(i): 0 for i in range(1, 46)}
    
    # 3. 데이터 수집 (최근 40회차)
    # 전체를 다시 계산하는게 아니라, 로컬 파일이 있으면 그걸 로드해서 갱신하는게 좋지만,
    # 여기서는 안전하게 최근 범위(RANGE)만 가지고 카운트 예시를 보여줍니다.
    # (실제 앱 로직에 맞춰 범위 조정 가능)
    
    # 기존 파일이 있다면 읽어서 누적하고 싶을 경우:
    # if os.path.exists(OUT): ... (생략, 필요시 추가)

    start_round = max(1, latest - 40 + 1)
    success_count = 0
    
    for rnd in range(start_round, latest + 1):
        js = fetch_round(scraper, rnd)
        
        if js.get("returnValue") != "success":
            print(f"[ERROR] Failed to fetch data for round {rnd} (Both Official & Naver failed)")
            continue
            
        success_count += 1
        # 번호 카운팅
        for k in [f"drwtNo{i}" for i in range(1, 7)]:
            val = js.get(k)
            if isinstance(val, int) and 1 <= val <= 45:
                counts[str(val)] += 1

    # 결과 저장
    out = {
        "meta": {
            "latestRound": latest,
            "range": 40,
            "updatedAt": now_kst_iso(),
        },
        "counts": counts,
    }

    # 하나라도 성공했다면 저장
    if success_count > 0:
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[SUCCESS] Updated heatmap.json with {success_count} rounds.")
    else:
        # 실패했다면 에러를 발생시켜 GitHub Action을 빨간색으로 만듦 (로그 확인용)
        raise RuntimeError("No data fetched! Check logs.")

if __name__ == "__main__":
    main()
