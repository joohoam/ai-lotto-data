import json
import os
import datetime
import requests

OUT = "data/heatmap.json"
API = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"

# [수정] 봇 차단 방지용 헤더 (User-Agent 구체화 + Referer 추가)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.dhlottery.co.kr/"
}

RANGE = 40  # ✅ 최근 40회

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def fetch_round(rnd: int) -> dict:
    # requests 호출 시 수정된 HEADERS가 사용됨
    r = requests.get(API.format(round=rnd), headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def guess_latest(start: int = 1200, max_tries: int = 80) -> int:
    cand = start
    for _ in range(max_tries):
        try:
            js = fetch_round(cand)
            if js.get("returnValue") == "success" and js.get("drwNo") == cand:
                # 다음 회차가 있는지 확인
                try:
                    js2 = fetch_round(cand + 1)
                    if js2.get("returnValue") == "success":
                        cand += 1
                        continue
                except:
                    pass # 다음 회차 조회 실패 시 현재 회차를 최신으로 간주할 수도 있음
                return cand
        except:
            pass # 조회 실패 시 재시도 혹은 이전 값 사용 로직 필요하지만 여기선 단순화
        cand -= 1
        
    # 기본적으로 실패 시 start 반환하거나 에러 발생 (기존 로직 유지)
    raise RuntimeError("latestRound 찾기 실패. start 값을 조정하세요.")

def now_kst_iso():
    kst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(kst).isoformat(timespec="seconds")

def main():
    ensure_dirs()

    # 이전 파일 meta.latestRound가 있으면 시작점으로 활용
    start = 1200
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                j = json.load(f) or {}
            start = int((j.get("meta", {}) or {}).get("latestRound", start))
        except Exception:
            pass

    latest = guess_latest(start=start)
    start_round = max(1, latest - RANGE + 1)

    counts = {str(i): 0 for i in range(1, 46)}

    for rnd in range(start_round, latest + 1):
        try:
            js = fetch_round(rnd)
        except Exception as e:
            print(f"Skipping round {rnd} due to error: {e}")
            continue

        if js.get("returnValue") != "success":
            continue
        nums = [
            js.get("drwtNo1"), js.get("drwtNo2"), js.get("drwtNo3"),
            js.get("drwtNo4"), js.get("drwtNo5"), js.get("drwtNo6"),
        ]
        for n in nums:
            if isinstance(n, int) and 1 <= n <= 45:
                counts[str(n)] += 1

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
