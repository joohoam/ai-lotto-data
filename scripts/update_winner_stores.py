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

# -----------------------------
# URLs
# -----------------------------
BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin"
LOTTO_API_URL = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={drwNo}"

TOPSTORE_BASE_URL = "https://dhlottery.co.kr/store.do"
TOPSTORE_GET_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645&drwNo={drwNo}"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
}

ONLINE_STORE_NAME_KEYWORDS = [
    "인터넷 복권판매사이트",
]
ONLINE_ADDR_KEYWORDS = [
    "dhlottery.co.kr",
    "동행복권(dhlottery.co.kr)",
    "동행복권 (dhlottery.co.kr)",
]


# -----------------------------
# HTTP helpers
# -----------------------------
def http_get(session: requests.Session, url: str, timeout: int = 25, retries: int = 4, backoff: float = 0.8) -> requests.Response:
    last = None
    for i in range(retries):
        try:
            r = session.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(backoff * (i + 1))
    raise RuntimeError(f"HTTP GET failed: {url} ({last})")


def http_post(session: requests.Session, url: str, data: dict, timeout: int = 25, retries: int = 4, backoff: float = 0.8) -> requests.Response:
    last = None
    for i in range(retries):
        try:
            headers = dict(DEFAULT_HEADERS)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Referer"] = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645"
            r = session.post(url, headers=headers, data=data, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(backoff * (i + 1))
    raise RuntimeError(f"HTTP POST failed: {url} data={data} ({last})")


def decode_html(resp: requests.Response) -> str:
    enc = (resp.encoding or "").lower().strip()
    if not enc or enc in ("iso-8859-1", "latin-1"):
        guess = (resp.apparent_encoding or "").lower().strip()
        if guess and guess not in ("ascii", "iso-8859-1", "latin-1"):
            resp.encoding = guess
        else:
            resp.encoding = "euc-kr"
    try:
        return resp.text
    except Exception:
        return resp.content.decode(resp.encoding or "euc-kr", errors="replace")


# -----------------------------
# Latest round
# -----------------------------
def get_latest_round_from_bywin(session: requests.Session) -> Optional[int]:
    html = decode_html(http_get(session, BYWIN_URL, timeout=20, retries=3, backoff=0.6))
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


def lotto_api_success(session: requests.Session, drw_no: int) -> bool:
    url = LOTTO_API_URL.format(drwNo=drw_no)
    r = http_get(session, url, timeout=20, retries=3, backoff=0.6)
    data = r.json()
    return data.get("returnValue") == "success"


def find_latest_round_by_api(session: requests.Session, max_hint: int = 2000) -> int:
    lo, hi = 1, max_hint
    while lotto_api_success(session, hi):
        lo = hi
        hi *= 2
        if hi > 10000:
            break
    left, right = lo, hi
    while left + 1 < right:
        mid = (left + right) // 2
        if lotto_api_success(session, mid):
            left = mid
        else:
            right = mid
    return left


def get_latest_round(session: requests.Session) -> int:
    latest = get_latest_round_from_bywin(session)
    return latest if latest is not None else find_latest_round_by_api(session)


# -----------------------------
# Models / Normalization
# -----------------------------
@dataclass
class RawRow:
    round: int
    rank: int
    store_name: str
    method: str
    address: str
    sido: str
    sigungu: str
    channel: str  # "online" or "offline"


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


def is_online_store(store_name: str, address: str) -> bool:
    sn = (store_name or "").strip()
    ad = (address or "").strip()

    if any(k in sn for k in ONLINE_STORE_NAME_KEYWORDS):
        return True
    # 주소에 동행복권 도메인/표기가 있으면 온라인
    if any(k in ad for k in ONLINE_ADDR_KEYWORDS):
        return True
    # 더 보수적으로: 주소에 도메인만 있어도 온라인
    if "dhlottery.co.kr" in ad:
        return True
    return False


def normalize_region(store_name: str, address: str) -> Tuple[str, str, str]:
    """
    return: (sido, sigungu, channel)
    - 인터넷 복권판매사이트는 무조건 channel=online / sido=온라인
    - 그 외는 주소 기반으로 sido/sigungu 파싱
    """
    if is_online_store(store_name, address):
        return "온라인", "", "online"

    addr = (address or "").strip()
    if not addr:
        return "", "", "offline"

    parts = addr.split()
    if not parts:
        return "", "", "offline"

    sido = _SIDO_ALIASES.get(parts[0], parts[0])
    sigungu = parts[1] if len(parts) >= 2 else ""
    return sido, sigungu, "offline"


def t(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


# -----------------------------
# Table picking & parsing
# -----------------------------
def pick_rank1_table(soup: BeautifulSoup):
    best = None
    best_rows = -1
    for table in soup.find_all("table"):
        headers = " ".join([t(th) for th in table.find_all("th")])
        if "구분" in headers and ("상호" in headers or "판매점" in headers):
            data_rows = [tr for tr in table.find_all("tr") if len(tr.find_all("td")) >= 4]
            if len(data_rows) > best_rows:
                best_rows = len(data_rows)
                best = table
    return best


def pick_rank2_table(soup: BeautifulSoup):
    best = None
    best_rows = -1
    for table in soup.find_all("table"):
        headers = " ".join([t(th) for th in table.find_all("th")])
        if ("상호" in headers or "판매점" in headers) and (("소재지" in headers) or ("주소" in headers)):
            if "구분" in headers:
                continue
            data_rows = [tr for tr in table.find_all("tr") if len(tr.find_all("td")) >= 3]
            if len(data_rows) > best_rows:
                best_rows = len(data_rows)
                best_rows = len(data_rows)
                best = table
    return best


def parse_rank1_rows(table, round_no: int) -> List[RawRow]:
    out: List[RawRow] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        store_name = t(tds[1])
        method = t(tds[2])
        address = t(tds[3])
        if not store_name or not address:
            continue

        sido, sigungu, channel = normalize_region(store_name, address)
        out.append(RawRow(round_no, 1, store_name, method, address, sido, sigungu, channel))
    return out


def parse_rank2_rows(table, round_no: int, limit_left: Optional[int]) -> List[RawRow]:
    out: List[RawRow] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        store_name = t(tds[1])

        addr_candidates = [t(tds[2])]
        if len(tds) >= 4:
            addr_candidates.append(t(tds[3]))
        address = next((a for a in addr_candidates if a), "")

        if not store_name or not address:
            continue

        sido, sigungu, channel = normalize_region(store_name, address)
        out.append(RawRow(round_no, 2, store_name, "", address, sido, sigungu, channel))

        if limit_left is not None and len(out) >= limit_left:
            break
    return out


# -----------------------------
# topStore fetching (pagination)
# -----------------------------
def fetch_topstore_page(session: requests.Session, drw_no: int, now_page: int) -> str:
    if now_page == 1:
        url = TOPSTORE_GET_URL.format(drwNo=drw_no)
        return decode_html(http_get(session, url))

    data = {
        "method": "topStore",
        "pageGubun": "L645",
        "drwNo": str(drw_no),
        "nowPage": str(now_page),
    }
    try:
        return decode_html(http_post(session, TOPSTORE_BASE_URL, data=data))
    except Exception:
        url = TOPSTORE_GET_URL.format(drwNo=drw_no) + f"&nowPage={now_page}"
        return decode_html(http_get(session, url))


def page_signature_rank2(rows: List[RawRow]) -> Tuple[Tuple[str, str], ...]:
    return tuple((r.store_name, r.address) for r in rows)


def crawl_one_round(
    session: requests.Session,
    drw_no: int,
    include_rank2: bool,
    rank2_limit: Optional[int],
    rank2_max_pages: int,
) -> Tuple[List[RawRow], List[dict], dict]:
    failures: List[dict] = []
    raw_rows: List[RawRow] = []

    dbg = {"rank2PagesFetched": 0, "rank2RowsFetched": 0, "rank2StoppedBy": ""}

    # page 1
    html1 = fetch_topstore_page(session, drw_no, 1)
    soup1 = BeautifulSoup(html1, "lxml")

    # rank1
    t1 = pick_rank1_table(soup1)
    if t1 is None:
        failures.append({"round": drw_no, "rank": 1, "page": 1, "reason": "Rank1 table not found"})
    else:
        raw_rows.extend(parse_rank1_rows(t1, drw_no))

    # rank2
    if include_rank2:
        t2 = pick_rank2_table(soup1)
        if t2 is None:
            failures.append({"round": drw_no, "rank": 2, "page": 1, "reason": "Rank2 table not found"})
            return raw_rows, failures, dbg

        limit_left = None if rank2_limit is None else max(0, rank2_limit)
        r2_page1 = parse_rank2_rows(t2, drw_no, limit_left)

        dbg["rank2PagesFetched"] += 1
        dbg["rank2RowsFetched"] += len(r2_page1)

        if not r2_page1:
            dbg["rank2StoppedBy"] = "empty_page_1"
            return raw_rows, failures, dbg

        raw_rows.extend(r2_page1)

        if rank2_limit is not None and sum(1 for r in raw_rows if r.rank == 2) >= rank2_limit:
            dbg["rank2StoppedBy"] = "limit_reached_on_page_1"
            return raw_rows, failures, dbg

        prev_sig = page_signature_rank2(r2_page1)

        page = 2
        while page <= rank2_max_pages:
            if rank2_limit is not None and sum(1 for r in raw_rows if r.rank == 2) >= rank2_limit:
                dbg["rank2StoppedBy"] = "limit_reached"
                break

            htmlp = fetch_topstore_page(session, drw_no, page)
            soup = BeautifulSoup(htmlp, "lxml")

            t2p = pick_rank2_table(soup)
            if t2p is None:
                dbg["rank2StoppedBy"] = "no_rank2_table"
                break

            if rank2_limit is not None:
                limit_left = max(0, rank2_limit - sum(1 for r in raw_rows if r.rank == 2))
            else:
                limit_left = None

            r2 = parse_rank2_rows(t2p, drw_no, limit_left)
            dbg["rank2PagesFetched"] += 1
            dbg["rank2RowsFetched"] += len(r2)

            if not r2:
                dbg["rank2StoppedBy"] = "empty_page"
                break

            sig = page_signature_rank2(r2)
            if sig == prev_sig:
                dbg["rank2StoppedBy"] = "repeated_page_signature"
                break

            raw_rows.extend(r2)
            prev_sig = sig

            page += 1
            time.sleep(0.15)

        if page > rank2_max_pages and not dbg["rank2StoppedBy"]:
            dbg["rank2StoppedBy"] = "max_pages_guard"

    return raw_rows, failures, dbg


# -----------------------------
# Aggregation: group + count
# -----------------------------
def aggregate_rows(rows: List[RawRow]) -> List[dict]:
    """
    (round, rank, storeName, address)로 묶고 count 집계.
    - 온라인(인터넷 복권판매사이트)은 channel=online, sido=온라인 유지
    """
    m: Dict[Tuple[int, int, str, str], dict] = {}

    for r in rows:
        key = (r.round, r.rank, r.store_name, r.address)
        if key not in m:
            m[key] = {
                "round": r.round,
                "rank": r.rank,
                "storeName": r.store_name,
                "method": r.method,
                "address": r.address,
                "sido": r.sido,
                "sigungu": r.sigungu,
                "channel": r.channel,   # ✅ online/offline
                "count": 1,             # ✅ 묶인 건수
            }
        else:
            m[key]["count"] += 1

    out = list(m.values())
    out.sort(key=lambda x: (x["rank"], -x["count"], x["storeName"]))
    return out


def build_json(range_n: int, include_rank2: bool, rank2_limit: Optional[int], rank2_max_pages: int) -> Dict:
    with requests.Session() as session:
        latest = get_latest_round(session)
        start = max(1, latest - range_n + 1)

        failures: List[dict] = []
        all_raw_rows: List[RawRow] = []
        per_round_debug: Dict[str, dict] = {}

        stats = {
            "requestedRounds": range_n,
            "startRound": start,
            "endRound": latest,
            "parsedRounds": 0,
            "rawRank1Rows": 0,
            "rawRank2Rows": 0,
            "aggItems": 0,
            "aggRank1Items": 0,
            "aggRank2Items": 0,
            "onlineAggRank1Items": 0,
            "onlineAggRank2Items": 0,
        }

        for drw_no in range(start, latest + 1):
            try:
                rows, fail, dbg = crawl_one_round(
                    session=session,
                    drw_no=drw_no,
                    include_rank2=include_rank2,
                    rank2_limit=rank2_limit,
                    rank2_max_pages=rank2_max_pages,
                )
                failures.extend(fail)
                all_raw_rows.extend(rows)
                per_round_debug[str(drw_no)] = dbg

                if rows:
                    stats["parsedRounds"] += 1

                stats["rawRank1Rows"] += sum(1 for r in rows if r.rank == 1)
                stats["rawRank2Rows"] += sum(1 for r in rows if r.rank == 2)

                time.sleep(0.2)
            except Exception as e:
                failures.append({"round": drw_no, "reason": str(e)})

        # round별 raw rows를 모아서 aggregate
        tmp_round: Dict[int, List[RawRow]] = {}
        for r in all_raw_rows:
            tmp_round.setdefault(r.round, []).append(r)

        by_round: Dict[str, List[dict]] = {}
        for rno, rrows in tmp_round.items():
            by_round[str(rno)] = aggregate_rows(rrows)

        # region별: 집계된 항목 기준
        by_region: Dict[str, List[dict]] = {}
        online_by_rank: Dict[str, List[dict]] = {"1": [], "2": []}  # ✅ 요청하신 "1등/2등 온라인 카테고리"

        for rno_str, items in by_round.items():
            for it in items:
                region_key = it.get("sido") or "기타"
                # 온라인은 region_key가 "온라인"으로 들어옴
                by_region.setdefault(region_key, []).append(
                    {
                        "round": it["round"],
                        "rank": it["rank"],
                        "storeName": it["storeName"],
                        "method": it.get("method", ""),
                        "address": it["address"],
                        "sigungu": it.get("sigungu", ""),
                        "channel": it.get("channel", "offline"),
                        "count": it.get("count", 1),
                    }
                )

                # ✅ 온라인은 별도 섹션으로도 제공 (안티그래비티에서 쉽게 쓰게)
                if it.get("channel") == "online":
                    rk = str(it.get("rank"))
                    if rk in online_by_rank:
                        online_by_rank[rk].append(
                            {
                                "round": it["round"],
                                "rank": it["rank"],
                                "storeName": it["storeName"],
                                "address": it["address"],
                                "count": it.get("count", 1),
                            }
                        )

        # 정렬: 최신 회차 우선
        for k in by_region:
            by_region[k].sort(key=lambda x: (x["round"], x["rank"], -x["count"]), reverse=True)
        for rk in online_by_rank:
            online_by_rank[rk].sort(key=lambda x: (x["round"], -x["count"]), reverse=True)

        # stats(집계 기반)
        all_items = [it for arr in by_round.values() for it in arr]
        stats["aggItems"] = len(all_items)
        stats["aggRank1Items"] = sum(1 for it in all_items if it["rank"] == 1)
        stats["aggRank2Items"] = sum(1 for it in all_items if it["rank"] == 2)

        stats["onlineAggRank1Items"] = sum(1 for it in all_items if it["rank"] == 1 and it.get("channel") == "online")
        stats["onlineAggRank2Items"] = sum(1 for it in all_items if it["rank"] == 2 and it.get("channel") == "online")

        now_iso = datetime.now(timezone.utc).isoformat()

        return {
            "meta": {
                "updatedAt": now_iso,
                "range": range_n,
                "latestRound": latest,
                "includeRank2": bool(include_rank2),
                "rank2Limit": rank2_limit,
                "rank2MaxPages": rank2_max_pages,
                "source": {
                    "topStoreGetUrlTemplate": TOPSTORE_GET_URL,
                    "topStoreBaseUrl": TOPSTORE_BASE_URL,
                    "byWinUrl": BYWIN_URL,
                    "lottoApiUrlTemplate": LOTTO_API_URL,
                },
                "stats": stats,
                "perRoundDebug": per_round_debug,
                "failures": failures,
            },
            "byRound": by_round,
            "byRegion": by_region,           # ✅ "온라인" 키로 따로 들어감
            "onlineByRank": online_by_rank,  # ✅ 요청사항: 인터넷 카테고리에서 1등/2등 분리 제공
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--range", type=int, default=int(os.getenv("WINNER_STORES_RANGE", "10")))
    parser.add_argument("--out", type=str, default="data/winner_stores.json")
    parser.add_argument("--no-rank2", action="store_true", help="Exclude rank2 store list")

    # 0이면 제한 없음
    parser.add_argument("--rank2-limit", type=int, default=int(os.getenv("WINNER_STORES_RANK2_LIMIT", "0")))
    # 끝 페이지 감지로 멈추지만 최악 대비 상한
    parser.add_argument("--rank2-max-pages", type=int, default=int(os.getenv("WINNER_STORES_RANK2_MAX_PAGES", "120")))

    args = parser.parse_args()

    rank2_limit = None if args.rank2_limit == 0 else max(0, args.rank2_limit)
    rank2_max_pages = max(1, args.rank2_max_pages)

    data = build_json(
        range_n=args.range,
        include_rank2=(not args.no_rank2),
        rank2_limit=rank2_limit,
        rank2_max_pages=rank2_max_pages,
    )

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    s = data["meta"]["stats"]
    print(f"[OK] wrote: {args.out}")
    print(
        f"latestRound={data['meta']['latestRound']} range={data['meta']['range']} "
        f"parsedRounds={s['parsedRounds']} rawRank2Rows={s['rawRank2Rows']} "
        f"aggItems={s['aggItems']} onlineRank2Items={s['onlineAggRank2Items']} failures={len(data['meta']['failures'])}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
