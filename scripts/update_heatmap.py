import json
import os
import datetime
import requests

OUT = "data/heatmap.json"
API = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def fetch_round(rnd: int) -> dict:
    r = requests.get(API.format(round=rnd), headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def get_latest_round_guess(max_tries: int = 30) -> int:
    """
    공식 API는 존재하지 않는 회차에 대해 fail을 주므로
    최근 회차 근처를 탐색해 latest를 찾습니다.
    """
    # 먼저 OUT에 기록된 latest가 있으면 그 근처부터 탐색
    start = 1200
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                j = json.load(f) or {}
                start = int(j.get("meta", {}).get("latestRound", start))
        except Exception:
            pass

    # 앞으로/뒤로 탐색
    cand = start
    for _ in range(max_tries):
        js = fetch_round(cand)
        if js.get("returnValue") == "success" and js.get("drwNo") == cand:
            # 더 최신이 있는지 +1 체크
            js2 = fetch_round(cand + 1)
            if js2.get("returnValue") == "success":
                cand += 1
                continue
            return cand
        cand -= 1

    raise RuntimeError("latestRound를 찾지 못했습니다. start 값을 조정하세요.")

def main():
    ensure_dirs()

    latest = get_latest_round_guess()
    rng = 50  # 최근 50회 기준 (원하면 바꾸세요)
    start = max(1, latest - rng + 1)

    counts = {str(i): 0 for i in range(1, 46)}

    for rnd in range(start, latest + 1):
        js = fetch_round(rnd)
        if js.get("returnValue") != "success":
            continue
        nums = [js.get(f"drwtNo{i}") for i in range(1, 7)]
        for n in nums:
            if isinstance(n, int) and 1 <= n <= 45:
                counts[str(n)] += 1

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")

    out = {
        "meta": {
            "latestRound": latest,
            "range": rng,
            "updatedAt": now
        },
        "counts": counts
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()

