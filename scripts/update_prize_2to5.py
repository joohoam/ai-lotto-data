import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup

OUT = "data/prize_2to5.json"

BASE = "https://dhlottery.co.kr/gameResult.do?method=byWin"
URL = "https://dhlottery.co.kr/gameResult.do?method=byWin&drwNo={round}"

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ✅ 최근 몇 회만 유지할지 (원하면 40 -> 80 등으로 변경)
WINDOW = 40

def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def to_int(s: str) -> int:
    digits = re.sub(r"[^\d]", "", s or "")
    return int(digits) if digits else 0

def get_latest_round() -> int:
    """
    byWin 페이지의 select option들 중 숫자 value를 전부 모아서 최댓값을 최신 회차로 사용.
    (첫 option/selected option을 쓰면 1회차를 잡는 경우가 있음)
    """
    r = requests.get(BASE, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    values = []
    for o in soup.select("select option"):
        v = (o.get("value") or "").strip()
        if v.isdigit():
            values.append(int(v))

    if values:
        return max(values)

    # fallback: “1200회” 같은 패턴
    m = re.search(r"(\d+)\s*회", soup.get_text(" ", strip=True))
    if m:
        return int(m.group(1))

    raise RuntimeError("최신 회차를 찾지 못했습니다.")

def parse_2to5(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("table.tbl_data tbody tr") or soup.select("table tbody tr")

    data = {}
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not tds:
            continue

        # 기대 형태: [등위, 총당첨금, 당첨게임 수, 1게임당 당첨금, 당첨기준]
        if tds[0] in ["2등", "3등", "4등", "5등"]:
            rank = tds[0].replace("등", "")
            total = to_int(tds[1]) if len(tds) > 1 else 0
            winners = to_int(tds[2]) if len(tds) > 2 else 0
            per = to_int(tds[3]) if len(tds) > 3 else 0
            criteria = tds[4] if len(tds) > 4 else ""

            data[rank] = {
                "totalPrize": total,
                "winners": winners,
                "perGamePrize": per,
                "criteria": criteria,
            }

    # 누락되면 구조 변경/파싱 실패로 판단
    for rnk in ["2", "3", "4", "5"]:
        if rnk not in data:
            raise RuntimeError(f"{rnk}등 파싱 실패(페이지 구조 변경 가능)")
    return data

def load_existing() -> dict:
    if not os.path.exists(OUT):
        return {}
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            j = json.load(f)
            return j if isinstance(j, dict) else {}
    except Exception:
        return {}

def prune_to_window(existing: dict, start_round: int, latest_round: int) -> dict:
    """
    ✅ 옵션 A: 최근 WINDOW회 범위(start_round~latest_round) 밖의 키를 제거
    """
    pruned = {}
    for k, v in existing.items():
        if not isinstance(k, str) or not k.isdigit():
            continue
        rn = int(k)
        if start_round <= rn <= latest_round:
            pruned[k] = v
    return pruned

def main():
    ensure_dirs()

    existing = load_existing()

    latest = get_latest_round()
    start = max(1, latest - WINDOW + 1)

    # ✅ 먼저 오래된 회차 데이터를 제거(옵션 A 핵심)
    existing = prune_to_window(existing, start, latest)

    # ✅ 최근 WINDOW회 범위만 채우기
    for rnd in range(start, latest + 1):
        key = str(rnd)

        # 이미 있고 2~5등 다 있으면 스킵
        if key in existing and isinstance(existing[key], dict) and all(k in existing[key] for k in ["2", "3", "4", "5"]):
            continue

        r = requests.get(URL.format(round=rnd), headers=HEADERS, timeout=20)
        r.raise_for_status()

        existing[key] = parse_2to5(r.text)
        time.sleep(0.25)  # 과도한 요청 방지

    # (선택) 보기 좋게 회차 오름차순으로 정렬 저장
    ordered = {str(k): existing[str(k)] for k in sorted((int(x) for x in existing.keys()), key=int)}

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
