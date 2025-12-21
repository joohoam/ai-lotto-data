#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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


def http_get(url: str, timeout: int = 20, retries: int = 3, sleep_sec: float = 0.6) -> requests.Response:
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
    enc = (resp.encoding or "").lower().strip()
    if not enc or enc in ("iso-8859-1", "latin-1"):
        resp.encoding = resp.apparent_encoding or "euc-kr"
    try:
        return resp.text
    except Exception:
        return resp.content.decode("euc-kr", errors="replace")


def get_latest_round_from_bywin() -> Optional[int]:
    html = decode_korean_html(http_get(BYWIN_URL))
    m = re.search(r"lottoDrwNo\s*=\s*(\d+)", html)
    if m:
        return int(m.group(1))
    m = re.search(r'id=["\']lottoDrwNo["\'][^>]*value=["\'](\d+)["\']', html)
    if m:
        return int(m.group(1))
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
    lo = 1
    hi = max_hint
    while lotto_api_success(hi):
        lo = hi
        hi *= 2
        if hi > 10000:
            break

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


@dataclass
class StoreRow:
    round: int
    rank: int  # 1 or 2
    store_name: str
    method: str  # 1등: 자동/수동, 2등: ""(대부분 없음)
    address: str
    sido: str
    sigungu: str


_SIDO_ALIASES = {
    "서울특별시": "서울", "서울시": "서울", "서울": "서울",
    "부산광역시": "부산", "부산": "부산",
    "대구광역시": "대구", "대구": "대구",
    "인천광역시": "인천", "인천": "인천",
    "광주광역시": "광주", "광주": "광주",
    "대전광역시": "대전", "대전": "대전",
    "울산광역시": "울산", "울산": "울산",
    "세종특별자치시": "세종", "세종": "세종",
    "경기도": "경기", "경기": "경기",
    "강원특별자치도": "강원", "강원도": "강원", "강원": "강원",
    "충청북도": "충북", "충북": "충북",
    "충청남도": "충남", "충남": "충남",
    "전라북도": "전북", "전북": "전북",
    "전라남도": "전남", "전남": "전남",
    "경상북도": "경북", "경북": "경북",
    "경상남도": "경남", "경남": "경남",
    "제주특별자치도": "제주", "제주도": "제주", "제주": "제주",
}


def normalize_region_from_address(address: str) -> Tuple[str, str]:
    addr = (address or "").strip()
    if not addr:
        return "", ""

    if "dhlottery.co.kr" in addr or "인터넷" in addr:
        return "온라인", ""

    parts = addr.split()
    if not parts:
        return "", ""

    first = parts[0]
    sido = _SIDO_ALIASES.get(first, first)
    sigungu = parts[1] if len(parts) >= 2 else ""
    return sido, sigungu


def _find_table_by_label(soup: BeautifulSoup, label_regex: str) -> Optional[BeautifulSoup]:
    # "1등 배출점" 또는 "2등 배출점" 라벨 근처 table 우선
    label_nodes = soup.find_all(string=re.compile(label_regex))
    for node in label_nodes:
        parent = node.parent
        if not parent:
            continue
        cand = parent.find_next("table")
        if cand and cand.find_all("th"):
            return cand
    return None


def _parse_table_rows_generic(table, round_no: int, rank: int) -> List[StoreRow]:
    """
    테이블 헤더를 보고 상호/구분/소재지 컬럼 위치를 유연하게 찾는다.
    - 1등 테이블: 보통 "상호", "구분", "소재지"
    - 2등 테이블: 보통 "상호", "소재지" (구분 없음)
    """
    # header index map
    headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
    headers = [re.sub(r"\s+", " ", h).strip() for h in headers]

    def find_idx(keys: List[str]) -> Optional[int]:
        for k in keys:
            for i, h in enumerate(headers):
                if k in h:
                    return i
        return None

    idx_name = find_idx(["상호", "상호명", "판매점"])
    idx_method = find_idx(["구분"])  # 1등에 주로 존재
    idx_addr = find_idx(["소재지", "주소", "소 재 지"])

    # fallback: 보통 구조가 [번호, 상호, (구분), 소재지, ...]
    if idx_name is None:
        idx_name = 1
    if idx_addr is None:
        idx_addr = 3 if (idx_method is not None) else 2

    rows: List[StoreRow] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        def safe_td(i: int) -> str:
            if 0 <= i < len(tds):
                return re.sub(r"\s+", " ", tds[i].get_text(" ", strip=True)).strip()
            return ""

        store_name = safe_td(idx_name)
        method = safe_td(idx_method) if idx_method is not None else ""
        address = safe_td(idx_addr)

        if not store_name and not address:
            continue

        sido, sigungu = normalize_region_from_address(address)

        rows.append(
            StoreRow(
                round=round_no,
                rank=rank,
                store_name=store_name,
                method=method,
                address=address,
                sido=sido,
                sigungu=sigungu,
            )
        )

    return rows


def parse_rank_tables(html: str, round_no: int, include_rank2: bool) -> List[StoreRow]:
    soup = BeautifulSoup(html, "lxml")

    out: List[StoreRow] = []

    # 1등
    t1 = _find_table_by_label(soup, r"1\s*등\s*배출점")
    if t1 is None:
        # fallback: "구분" 헤더 포함 table을 1등으로 가정
        for cand in soup.find_all("table"):
            ths = [th.get_text(" ", strip=True) for th in cand.find_all("th")]
            if any("구분" in t for t in ths):
                t1 = cand
                break
    if t1 is not None:
        out.extend(_parse_table_rows_generic(t1, round_no, rank=1))

    # 2등
    if include_rank2:
        t2 = _find_table_by_label(soup, r"2\s*등\s*배출점")
        if t2 is not None:
            out.extend(_parse_table_rows_generic(t2, round_no, rank=2))

    return out


def update_winner_stores(range_n: int, include_rank2: bool) -> Dict:
    latest = get_latest_round()
    start = max(1, latest - range_n + 1)

    all_rows: List[StoreRow] = []
    failures: List[Dict] = []

    for drw_no in range(start, latest + 1):
        url = TOPSTORE_URL.format(drwNo=drw_no)
        try:
            resp = http_get(url, timeout=25, retries=3, sleep_sec=0.8)
            html = decode_korean_html(resp)

            rows = parse_rank_tables(html, drw_no, include_rank2=include_rank2)
            if not rows:
                failures.append({"round": drw_no, "reason": "No table parsed"})
            all_rows.extend(rows)

            time.sleep(0.25)
        except Exception as e:
            failures.append({"round": drw_no, "reason": str(e)})

    # ------- flat maps (호환) -------
    by_round_flat: Dict[str, List[Dict]] = {}
    by_region_flat: Dict[str, List[Dict]] = {}

    for r in all_rows:
        item = {
            "round": r.round,
            "rank": r.rank,
            "storeName": r.store_name,
            "method": r.method,  # 2등은 보통 ""
            "address": r.address,
            "sido": r.sido,
            "sigungu": r.sigungu,
        }
        by_round_flat.setdefault(str(r.round), []).append(item)

        region_key = r.sido or "기타"
        by_region_flat.setdefault(region_key, []).append(
            {
                "round": r.round,
                "rank": r.rank,
                "storeName": r.store_name,
                "method": r.method,
                "address": r.address,
                "sigungu": r.sigungu,
            }
        )

    for k in by_region_flat:
        by_region_flat[k].sort(key=lambda x: x["round"], reverse=True)

    # ------- ranked maps (편의) -------
    by_round_ranked: Dict[str, Dict[str, List[Dict]]] = {}
    by_region_ranked: Dict[str, Dict[str, List[Dict]]] = {}

    for r in all_rows:
        rr = str(r.round)
        rk = str(r.rank)

        by_round_ranked.setdefault(rr, {}).setdefault(rk, []).append(
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

        region_key = r.sido or "기타"
        by_region_ranked.setdefault(region_key, {}).setdefault(rk, []).append(
            {
                "round": r.round,
                "rank": r.rank,
                "storeName": r.store_name,
                "method": r.method,
                "address": r.address,
                "sigungu": r.sigungu,
            }
        )

    for region_key in by_region_ranked:
        for rk in by_region_ranked[region_key]:
            by_region_ranked[region_key][rk].sort(key=lambda x: x["round"], reverse=True)

    now_iso = datetime.now(timezone.utc).isoformat()

    return {
        "meta": {
            "updatedAt": now_iso,
            "range": range_n,
            "latestRound": latest,
            "includeRank2": bool(include_rank2),
            "source": {
                "topStoreUrlTemplate": TOPSTORE_URL,
                "byWinUrl": BYWIN_URL,
                "lottoApiUrlTemplate": LOTTO_API_URL,
            },
            "failures": failures,
        },
        # 호환(flat)
        "byRound": by_round_flat,
        "byRegion": by_region_flat,
        # 편의(ranked)
        "byRoundRanked": by_round_ranked,
        "byRegionRanked": by_region_ranked,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--range", type=int, default=int(os.getenv("WINNER_STORES_RANGE", "10")))
    parser.add_argument("--out", type=str, default="data/winner_stores.json")
    # 기본: 2등 포함(원하면 --no-rank2로 끌 수 있음)
    parser.add_argument("--no-rank2", action="store_true", help="Do not include rank2 store list")
    args = parser.parse_args()

    data = update_winner_stores(args.range, include_rank2=(not args.no_rank2))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] wrote: {args.out}")
    print(
        f"      latestRound={data['meta']['latestRound']} range={data['meta']['range']} "
        f"includeRank2={data['meta']['includeRank2']} failures={len(data['meta']['failures'])}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
