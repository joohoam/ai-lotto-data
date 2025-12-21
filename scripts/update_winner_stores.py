#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Update Lotto 6/45 winner stores (1st prize outlets) for the latest N rounds.

- Source page (by round): https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645&drwNo=XXXX
  Contains both 1st and 2nd prize sections. We parse the "1등 배출점" table (includes "구분" column).
  :contentReference[oaicite:2]{index=2}

- Latest round discovery:
  1) Try parsing byWin page: https://dhlottery.co.kr/gameResult.do?method=byWin  :contentReference[oaicite:3]{index=3}
  2) Fallback: probe common.do API with binary search:
     https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo=903 :contentReference[oaicite:4]{index=4}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin"
LOTTO_API_URL = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={drwNo}"
TOPSTORE_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645&drwNo={drwNo}"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
}


# -------------------------
# Helpers: HTTP
# -------------------------
def http_get(url: str, timeout: int = 20, retries: int = 3, sleep_sec: float = 0.5) -> requests.Response:
    last_exc = None
    for i in range(retries):
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            if i < retries - 1:
                time.sleep(sleep_sec * (i + 1))
    raise RuntimeError(f"HTTP GET failed: {url} ({last_exc})")


def decode_korean_html(resp: requests.Response) -> str:
    """
    dhlottery pages may not declare encoding consistently.
    Force a reasonable decoding strategy.
    """
    # requests sometimes sets ISO-8859-1 by default when no charset is declared
    enc = (resp.encoding or "").lower().strip()
    if not enc or enc in ("iso-8859-1", "latin-1"):
        # Most of dhlottery pages are EUC-KR or UTF-8; use apparent_encoding first
        resp.encoding = resp.apparent_encoding or "euc-kr"
    try:
        return resp.text
    except Exception:
        # last resort
        return resp.content.decode("euc-kr", errors="replace")


# -------------------------
# Latest round discovery
# -------------------------
def get_latest_round_from_bywin() -> Optional[int]:
    """
    Try to parse latest drwNo from byWin page HTML.
    """
    html = decode_korean_html(http_get(BYWIN_URL))
    # Common patterns seen: lottoDrwNo=1234 or id="lottoDrwNo" value="1234"
    m = re.search(r"lottoDrwNo\s*=\s*(\d+)", html)
    if m:
        return int(m.group(1))
    m = re.search(r'id=["\']lottoDrwNo["\'][^>]*value=["\'](\d+)["\']', html)
    if m:
        return int(m.group(1))

    # Some pages include "회 당첨결과" near the top
    m = re.search(r"(\d+)\s*회\s*당첨결과", html)
    if m:
        return int(m.group(1))

    return None


def lotto_api_success(drw_no: int) -> bool:
    url = LOTTO_API_URL.format(drwNo=drw_no)
    resp = http_get(url)
    data = resp.json()
    return data.get("returnValue") == "success"


def find_latest_round_by_api(max_hint: int = 2000) -> int:
    """
    Fallback method:
    - Exponential search up to find an upper bound where API fails
    - Binary search to find last success
    """
    lo = 1
    hi = max_hint

    # If max_hint still succeeds, expand
    while lotto_api_success(hi):
        lo = hi
        hi *= 2
        if hi > 10000:
            # sanity bound
            break

    # Now binary search (lo success, hi maybe fail)
    left = lo
    right = hi
    while left + 1 < right:
        mid = (left + right) // 2
        if lotto_api_success(mid):
            left = mid
        else:
            right = mid

    return left


def get_latest_round() -> int:
    latest = get_latest_round_from_bywin()
    if latest is not None:
        return latest
    return find_latest_round_by_api()


# -------------------------
# Parsing / Normalization
# -------------------------
@dataclass
class StoreRow:
    round: int
    rank: int
    store_name: str
    method: str
    address: str
    sido: str
    sigungu: str


_SIDO_ALIASES = {
    "서울특별시": "서울",
    "서울시": "서울",
    "서울": "서울",
    "부산광역시": "부산",
    "부산": "부산",
    "대구광역시": "대구",
    "대구": "대구",
    "인천광역시": "인천",
    "인천": "인천",
    "광주광역시": "광주",
    "광주": "광주",
    "대전광역시": "대전",
    "대전": "대전",
    "울산광역시": "울산",
    "울산": "울산",
    "세종특별자치시": "세종",
    "세종": "세종",
    "경기도": "경기",
    "경기": "경기",
    "강원특별자치도": "강원",
    "강원도": "강원",
    "강원": "강원",
    "충청북도": "충북",
    "충북": "충북",
    "충청남도": "충남",
    "충남": "충남",
    "전라북도": "전북",
    "전북": "전북",
    "전라남도": "전남",
    "전남": "전남",
    "경상북도": "경북",
    "경북": "경북",
    "경상남도": "경남",
    "경남": "경남",
    "제주특별자치도": "제주",
    "제주도": "제주",
    "제주": "제주",
}


def normalize_region_from_address(address: str) -> Tuple[str, str]:
    """
    Extract sido/sigungu from a Korean address.
    For online sales (e.g. '동행복권(dhlottery.co.kr)'), return ('온라인', '').
    """
    addr = (address or "").strip()
    if not addr:
        return "", ""

    # Online / website
    if "dhlottery.co.kr" in addr or "인터넷" in addr:
        return "온라인", ""

    # Typical: "서울 강남구 ..." or "경기 성남시 ..."
    parts = addr.split()
    if not parts:
        return "", ""

    first = parts[0]
    sido = _SIDO_ALIASES.get(first, first)  # keep as-is if unknown
    sigungu = parts[1] if len(parts) >= 2 else ""
    return sido, sigungu


def parse_first_prize_table(html: str, round_no: int) -> List[StoreRow]:
    soup = BeautifulSoup(html, "lxml")

    # Find the "1등 배출점" label, then its next table
    table = None
    label_nodes = soup.find_all(string=re.compile(r"1\s*등\s*배출점"))
    for node in label_nodes:
        parent = node.parent
        if not parent:
            continue
        cand = parent.find_next("table")
        if cand and cand.find_all("th"):
            # Prefer table containing "구분" header
            th_texts = [th.get_text(strip=True) for th in cand.find_all("th")]
            if any("구분" in t for t in th_texts):
                table = cand
                break

    # Fallback: choose the first table that contains a "구분" header
    if table is None:
        for cand in soup.find_all("table"):
            th_texts = [th.get_text(strip=True) for th in cand.find_all("th")]
            if any("구분" in t for t in th_texts):
                table = cand
                break

    if table is None:
        return []

    rows: List[StoreRow] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        # Expected columns for 1등: 번호 | 상호명 | 구분 | 소재지 | 위치보기(옵션)
        store_name = tds[1].get_text(" ", strip=True)
        method = tds[2].get_text(" ", strip=True)
        address = tds[3].get_text(" ", strip=True)

        # Clean up
        store_name = re.sub(r"\s+", " ", store_name).strip()
        method = re.sub(r"\s+", " ", method).strip()
        address = re.sub(r"\s+", " ", address).strip()

        sido, sigungu = normalize_region_from_address(address)

        rows.append(
            StoreRow(
                round=round_no,
                rank=1,
                store_name=store_name,
                method=method,
                address=address,
                sido=sido,
                sigungu=sigungu,
            )
        )

    return rows


def update_winner_stores(range_n: int) -> Dict:
    latest = get_latest_round()
    start = max(1, latest - range_n + 1)

    all_rows: List[StoreRow] = []
    failures: List[Dict] = []

    for drw_no in range(start, latest + 1):
        url = TOPSTORE_URL.format(drwNo=drw_no)
        try:
            resp = http_get(url, timeout=25, retries=3, sleep_sec=0.6)
            html = decode_korean_html(resp)
            rows = parse_first_prize_table(html, drw_no)

            # Even if rows empty, keep record for debugging
            if not rows:
                failures.append({"round": drw_no, "reason": "No 1st-prize table parsed"})
            all_rows.extend(rows)

            time.sleep(0.25)  # polite delay
        except Exception as e:
            failures.append({"round": drw_no, "reason": str(e)})

    # Build by_round
    by_round: Dict[str, List[Dict]] = {}
    for r in all_rows:
        by_round.setdefault(str(r.round), []).append(
            {
                "round": r.round,
                "rank": r.rank,
                "storeName": r.store_name,
                "method": r.method,
                "address": r.address,
                "sido": r.sido,
                "sigungu": r.sigungu,
            }
        )

    # Build by_region (latest range only)
    by_region: Dict[str, List[Dict]] = {}
    for r in all_rows:
        key = r.sido or "기타"
        by_region.setdefault(key, []).append(
            {
                "round": r.round,
                "rank": r.rank,
                "storeName": r.store_name,
                "method": r.method,
                "address": r.address,
                "sigungu": r.sigungu,
            }
        )

    # Sort each region list by round desc
    for key in list(by_region.keys()):
        by_region[key].sort(key=lambda x: x["round"], reverse=True)

    now_iso = datetime.now(timezone.utc).isoformat()

    return {
        "meta": {
            "updatedAt": now_iso,
            "range": range_n,
            "latestRound": latest,
            "source": {
                "topStoreUrlTemplate": TOPSTORE_URL,
                "byWinUrl": BYWIN_URL,
                "lottoApiUrlTemplate": LOTTO_API_URL,
            },
            "failures": failures,  # keep for transparency (can be removed if you prefer)
        },
        "byRound": by_round,
        "byRegion": by_region,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--range", type=int, default=10, help="Number of latest rounds to fetch (default: 10)")
    parser.add_argument("--out", type=str, default="data/winner_stores.json", help="Output JSON path")
    args = parser.parse_args()

    data = update_winner_stores(args.range)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] wrote: {args.out}")
    print(f"      latestRound={data['meta']['latestRound']} range={data['meta']['range']} failures={len(data['meta']['failures'])}")


if __name__ == "__main__":
    main()
