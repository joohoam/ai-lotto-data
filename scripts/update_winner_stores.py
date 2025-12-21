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


def http_get(url: str, timeout: int = 25, retries: int = 4, backoff: float = 0.8) -> requests.Response:
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(backoff * (i + 1))
    raise RuntimeError(f"HTTP GET failed: {url} ({last})")


def decode_html(resp: requests.Response) -> str:
    # 동행복권 페이지는 EUC-KR/UTF-8 혼재 가능성이 있어 "보수적으로" 처리
    enc = (resp.encoding or "").lower().strip()
    if not enc or enc in ("iso-8859-1", "latin-1"):
        # requests가 인코딩을 못 잡는 경우가 잦아서 보정
        guess = (resp.apparent_encoding or "").lower().strip()
        if guess and guess not in ("ascii", "iso-8859-1", "latin-1"):
            resp.encoding = guess
        else:
            resp.encoding = "euc-kr"
    try:
        return resp.text
    except Exception:
        return resp.content.decode(resp.encoding or "euc-kr", errors="replace")


def get_latest_round_from_bywin() -> Optional[int]:
    html = decode_html(http_get(BYWIN_URL))
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
    r = http_get(url, timeout=20, retries=3, backoff=0.6)
    data = r.json()
    return data.get("returnValue") == "success"


def find_latest_round_by_api(max_hint: int = 2000) -> int:
    lo, hi = 1, max_hint
    while lotto_api_success(hi):
        lo = hi
        hi *= 2
        if hi > 10000:
            break
    left, right = lo, hi
    while left + 1 < right:
        mid = (left + right) // 2
        if lotto_api_success(mid):
            left = mid
        else:
            right = mid
    return left


def get_latest_round() -> int:
    latest = get_latest_round_from_bywin()
    return latest if latest is not None else find_latest_round_by_api()


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
    sido = _SIDO_ALIASES.get(parts[0], parts[0])
    sigungu = parts[1] if len(parts) >= 2 else ""
    return sido, sigungu


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _score_table(table) -> dict:
    """문자 기반이 아니라 '구조' 기반으로 후보 테이블을 점수화."""
    ths = [_text(th) for th in table.find_all("th")]
    trs = table.find_all("tr")
    td_rows = [tr.find_all("td") for tr in trs]
    # 데이터 행( td가 있는 행 ) 개수
    data_rows = [r for r in td_rows if len(r) >= 2]

    max_cols = max((len(r) for r in data_rows), default=0)
    header_join = " ".join(ths)

    # 신호들(있으면 가산점)
    has_name = ("상호" in header_join) or ("상호명" in header_join) or ("판매점" in header_join)
    has_addr = ("소재지" in header_join) or ("주소" in header_join)
    has_method = ("구분" in header_join) or ("선택" in header_join)

    score = 0
    score += min(len(data_rows), 50)               # 행이 많을수록
    score += 20 if max_cols >= 4 else 0            # 1등 테이블 후보
    score += 10 if max_cols == 3 else 0            # 2등 테이블 후보(대개 3열)
    score += 10 if has_name else 0
    score += 10 if has_addr else 0
    score += 5 if has_method else 0

    return {
        "score": score,
        "data_rows": data_rows,
        "max_cols": max_cols,
        "has_method": has_method,
        "headers": ths,
    }


def _parse_rank1_from_table(table, round_no: int) -> List[StoreRow]:
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        store_name = _text(tds[1])
        method = _text(tds[2])
        address = _text(tds[3])
        if not store_name or not address:
            continue
        sido, sigungu = normalize_region_from_address(address)
        rows.append(StoreRow(round_no, 1, store_name, method, address, sido, sigungu))
    return rows


def _parse_rank2_from_table(table, round_no: int, limit: Optional[int]) -> List[StoreRow]:
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        # 2등은 보통 [번호, 상호명, 소재지, ...] 형태가 많음
        if len(tds) < 3:
            continue
        store_name = _text(tds[1])
        # 주소는 2 또는 3번 인덱스에 있을 수 있어 유연하게
        cand_addrs = []
        cand_addrs.append(_text(tds[2]))
        if len(tds) >= 4:
            cand_addrs.append(_text(tds[3]))
        address = next((a for a in cand_addrs if a), "")
        if not store_name or not address:
            continue
        sido, sigungu = normalize_region_from_address(address)
        rows.append(StoreRow(round_no, 2, store_name, "", address, sido, sigungu))
        if limit is not None and len(rows) >= limit:
            break
    return rows


def parse_rank_tables(html: str, round_no: int, include_rank2: bool, rank2_limit: Optional[int]) -> List[StoreRow]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return []

    # 모든 테이블을 점수화
    scored = []
    for t in tables:
        info = _score_table(t)
        if info["max_cols"] >= 3 and info["score"] >= 20:
            scored.append((info["score"], info["max_cols"], info["has_method"], t, info))

    if not scored:
        return []

    # 점수순 정렬
    scored.sort(key=lambda x: x[0], reverse=True)

    # 1등: "4열 이상"이면서 method(구분) 신호가 있거나, 점수가 가장 높은 4열 테이블
    rank1_table = None
    for _, max_cols, has_method, t, _info in scored:
        if max_cols >= 4 and has_method:
            rank1_table = t
            break
    if rank1_table is None:
        for _, max_cols, _has_method, t, _info in scored:
            if max_cols >= 4:
                rank1_table = t
                break

    out: List[StoreRow] = []
    if rank1_table is not None:
        out.extend(_parse_rank1_from_table(rank1_table, round_no))

    # 2등: "3열 이상" 테이블 중에서 rank1과 다른 테이블을 후보로 선택
    if include_rank2:
        rank2_table = None
        for _, max_cols, _has_method, t, _info in scored:
            if t is rank1_table:
                continue
            if max_cols >= 3:
                rank2_table = t
                break
        if rank2_table is not None:
            out.extend(_parse_rank2_from_table(rank2_table, round_no, rank2_limit))

    return out


def update_winner_stores(range_n: int, include_rank2: bool, rank2_limit: Optional[int]) -> Dict:
    latest = get_latest_round()
    start = max(1, latest - range_n + 1)

    all_rows: List[StoreRow] = []
    failures: List[Dict] = []

    stats = {
        "requestedRounds": range_n,
        "startRound": start,
        "endRound": latest,
        "parsedRounds": 0,
        "rank1Rows": 0,
        "rank2Rows": 0,
    }

    for drw_no in range(start, latest + 1):
        url = TOPSTORE_URL.format(drwNo=drw_no)
        try:
            resp = http_get(url)
            html = decode_html(resp)

            rows = parse_rank_tables(html, drw_no, include_rank2=include_rank2, rank2_limit=rank2_limit)
            if not rows:
                failures.append({"round": drw_no, "reason": "No table parsed"})
            else:
                stats["parsedRounds"] += 1
                stats["rank1Rows"] += sum(1 for r in rows if r.rank == 1)
                stats["rank2Rows"] += sum(1 for r in rows if r.rank == 2)

            all_rows.extend(rows)
            time.sleep(0.25)
        except Exception as e:
            failures.append({"round": drw_no, "reason": str(e)})

    by_round: Dict[str, List[Dict]] = {}
    by_region: Dict[str, List[Dict]] = {}

    for r in all_rows:
        item = {
            "round": r.round,
            "rank": r.rank,
            "storeName": r.store_name,
            "method": r.method,
            "address": r.address,
            "sido": r.sido,
            "sigungu": r.sigungu,
        }
        by_round.setdefault(str(r.round), []).append(item)

        region_key = r.sido or "기타"
        by_region.setdefault(region_key, []).append(
            {
                "round": r.round,
                "rank": r.rank,
                "storeName": r.store_name,
                "method": r.method,
                "address": r.address,
                "sigungu": r.sigungu,
            }
        )

    for k in by_region:
        by_region[k].sort(key=lambda x: x["round"], reverse=True)

    now_iso = datetime.now(timezone.utc).isoformat()

    return {
        "meta": {
            "updatedAt": now_iso,
            "range": range_n,
            "latestRound": latest,
            "includeRank2": bool(include_rank2),
            "rank2Limit": rank2_limit,
            "source": {
                "topStoreUrlTemplate": TOPSTORE_URL,
                "byWinUrl": BYWIN_URL,
                "lottoApiUrlTemplate": LOTTO_API_URL,
            },
            "stats": stats,
            "failures": failures,
        },
        "byRound": by_round,
        "byRegion": by_region,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--range", type=int, default=int(os.getenv("WINNER_STORES_RANGE", "10")))
    parser.add_argument("--out", type=str, default="data/winner_stores.json")
    parser.add_argument("--no-rank2", action="store_true")
    parser.add_argument(
        "--rank2-limit",
        type=int,
        default=int(os.getenv("WINNER_STORES_RANK2_LIMIT", "0")),
        help="0 means no limit. Example: 50",
    )
    args = parser.parse_args()

    rank2_limit = None if (args.rank2_limit == 0) else args.rank2_limit

    data = update_winner_stores(
        range_n=args.range,
        include_rank2=(not args.no_rank2),
        rank2_limit=rank2_limit,
    )

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] wrote: {args.out}")
    s = data["meta"]["stats"]
    print(
        f"      latestRound={data['meta']['latestRound']} range={data['meta']['range']} "
        f"parsedRounds={s['parsedRounds']} rank1Rows={s['rank1Rows']} rank2Rows={s['rank2Rows']} "
        f"failures={len(data['meta']['failures'])}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
