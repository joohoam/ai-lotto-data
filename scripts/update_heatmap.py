import json
import os
import datetime
import requests

OUT = "data/heatmap.json"
API = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ✅ 최근 몇 회 기준으로 히트맵(출현 빈도) 만들지
WINDOW = 40

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def fetch_round(rnd: int) -> dict:
    r = requests.get(API.format(round=rnd), headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def get_latest_round_guess(max_tries: int = 60) -> int:
    """
    존재하는 최신 회차를 탐색해서 찾는다.
    (API는 없는 회차는 returnValue != success)
    """
    start = 1200
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                j = json.load(f) or {}
                start = int(j.get("meta", {}).get("latestRound", start))
        except Exception:
            pass

    cand = start
    for _ in range(max_tries):
        js = fetch_round(cand)
        if js.get("returnValue") == "success" and js.get("drwNo") == cand:
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
    start = max(1, latest - WINDOW + 1)

    counts = {str(i): 0 for i in range(1, 46)}

    for rnd in range(start, latest + 1):
        js = fetch_round(rnd)
        if js.get("returnValue") != "success":
            continue

        nums = [js.get(f"drwtNo{i}") for i in range(1, 7)]
        for n in nums:
            if isinstance(n, int) and 1 <= n <= 45:
                counts[str(n)] += 1

    now = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    ).isoformat(timespec="seconds")

    out = {
        "meta": {
            "latestRound": latest,
            "range": WINDOW,
            "updatedAt": now
        },
        "counts": counts
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
