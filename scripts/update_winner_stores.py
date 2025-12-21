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
from typing import Dict, List, Optional, Tuple, Set

import requests
from bs4 import BeautifulSoup

# -----------------------------
# URLs
# -----------------------------
BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin"
LOTTO_API_URL = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={drwNo}"

# topStore (회차별 당첨 판매점)
# 페이지 1은 GET로도 접근 가능하지만, 2등 페이지네이션은 POST nowPage로 동작하는 경우가 많아
# GET(1페이지) + POST(2페이지~) 혼합을 지원합니다.
TOPSTORE_BASE_URL = "https://dhlottery.co.kr/store.do"
TOPSTORE_GET_URL = "https://dhlottery.co.kr/store.do?method=topStore&pageGubun=L645&drwNo={drwNo}"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
}

# -----------------------------
# Helpers: HTTP
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
            # referer 힌트
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
    # 동행복권 페이지는 EUC-KR/UTF-8 혼재 가능성 → 보수적으로 처리
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
class StoreRow:
    round: int
    rank: int  # 1 or 2
    store_name: str
    method: str  # 1등: 자동/수동, 2등: 보통 ""
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


def t(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


# -----------------------------
# Parsing tables (robust)
# -----------------------------
def pick_rank1_table(soup: BeautifulSoup):
    # 1등 테이블은 보통 header에 "구분"이 있음
    candidates = []
    for table in soup.find_all("table"):
        headers = [t(th) for th in table.find_all("th")]
        header_join = " ".join(headers)
        if "구분" in header_join and ("상호" in header_join or "판매점" in header_join):
            # 데이터 행 수 기준으로 가장 큰 테이블을 선택
            data_rows = [tr for tr in table.find_all("tr") if len(tr.find_all("td")) >= 4]
            candidates.append((len(data_rows), table))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1] if candidates else None


def pick_rank2_table(soup: BeautifulSoup):
    # 2등 테이블은 보통 header에 "상호" + ("소재지" or "주소") 있고 "구분"은 없음
    candidates = []
    for table in soup.find_all("table"):
        headers = [t(th) for th in table.find_all("th")]
        header_join = " ".join(headers)
        if ("상호" in header_join or "판매점" in header_join) and (("소재지" in header_join) or ("주소" in header_join)):
            if "구분" in header_join:
                continue
            data_rows = [tr for tr in table.find_all("tr") if len(tr.find_all("td")) >= 3]
            candidates.append((len(data_rows), table))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1] if candidates else None


def parse_rank1_rows(table, round_no: int) -> List[StoreRow]:
    out: List[StoreRow] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        store_name = t(tds[1])
        method = t(tds[2])
        address = t(tds[3])
        if not store_name or not address:
            continue
        sido, sigungu = normalize_region_from_address(address)
        out.append(StoreRow(round_no, 1, store_name, method, address, sido, sigungu))
    return out


def parse_rank2_rows(table, round_no: int, limit_left: Optional[int]) -> List[StoreRow]:
    out: List[StoreRow] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        store_name = t(tds[1])
        # 주소 컬럼 위치가 가변적이라 2~3 중 유효한 것을 채택
        addr_candidates = [t(tds[2])]
        if len(tds) >= 4:
            addr_candidates.append(t(tds[3]))
        address = next((a for a in addr_candidates if a), "")
        if not store_name or not address:
            continue
        sido, sigungu = normalize_region_from_address(address)
        out.append(StoreRow(round_no, 2, store_name, "", address, sido, sigungu))

        if limit_left is not None and len(out) >= limit_left:
            break
    return out


# -----------------------------
# Fetch topStore page (pagination)
# -----------------------------
def fetch_topstore_page(session: requests.Session, drw_no: int, now_page: int) -> str:
    if now_page == 1:
        url = TOPSTORE_GET_URL.format(drwNo=drw_no)
        return decode_html(http_get(session, url))
    # 페이지 2부터는 POST nowPage로 시도 (실패하면 GET fallback도 한 번)
    data = {
        "method": "topStore",
        "pageGubun": "L645",
        "drwNo": str(drw_no),
        "nowPage": str(now_page),
    }
    try:
        return decode_html(http_post(session, TOPSTORE_BASE_URL, data=data))
    except Exception:
        # fallback: 일부 환경에서 GET 파라미터로 nowPage가 먹는 경우도 있어 시도
        url = TOPSTORE_GET_URL.format(drwNo=drw_no) + f"&nowPage={now_page}"
        return decode_html(http_get(session, url))


def parse_round(session: requests.Session, drw_no: int, include_rank2: bool, rank2_limit: Optional[int], rank2_max_pages: int) -> Tuple[List[StoreRow], List[dict]]:
    """
    - 1등: page=1에서 한 번만 파싱
    - 2등: page=1..N까지 반복, "새로운 정보가 없으면 멈춤"
    """
    failures: List[dict] = []
    rows: List[StoreRow] = []
    seen: Set[Tuple[int, int, str, str]] = set()  # (round, rank, name, addr)

    # --- page 1 ---
    html1 = fetch_topstore_page(session, drw_no, now_page=1)
    soup1 = BeautifulSoup(html1, "lxml")

    # rank1
    t1 = pick_rank1_table(soup1)
    if t1 is not None:
        r1 = parse_rank1_rows(t1, drw_no)
        for r in r1:
            key = (r.round, r.rank, r.store_name, r.address)
            if key not in seen:
                seen.add(key)
                rows.append(r)
    else:
        failures.append({"round": drw_no, "rank": 1, "page": 1, "reason": "Rank1 table not found"})

    # rank2 page 1
    if include_rank2:
        limit_left = None if rank2_limit is None else max(0, rank2_limit)
        t2 = pick_rank2_table(soup1)
        if t2 is not None:
            r2 = parse_rank2_rows(t2, drw_no, limit_left)
            for r in r2:
                key = (r.round, r.rank, r.store_name, r.address)
                if key not in seen:
                    seen.add(key)
                    rows.append(r)
            if rank2_limit is not None:
                limit_left = max(0, rank2_limit - sum(1 for r in rows if r.rank == 2))
        else:
            # 2등이 0명인 회차도 드물지만, 대부분 존재하므로 실패로 기록
            failures.append({"round": drw_no, "rank": 2, "page": 1, "reason": "Rank2 table not found"})

        # --- rank2 pagination pages ---
        # 끝 페이지에 정보가 없으면 멈춤:
        # - 해당 페이지에서 rank2 파싱 결과가 0이면 중단
        # - 파싱은 됐지만 '새로 추가된 항목'이 0이면 중단 (중복/끝)
        # - rank2_limit에 도달하면 중단
        if include_rank2:
            page = 2
            while page <= rank2_max_pages:
                if rank2_limit is not None and sum(1 for r in rows if r.rank == 2) >= rank2_limit:
                    break

                htmlp = fetch_topstore_page(session, drw_no, now_page=page)
                soup = BeautifulSoup(htmlp, "lxml")

                t2p = pick_rank2_table(soup)
                if t2p is None:
                    # 페이지에 2등 테이블이 없으면 끝으로 판단
                    break

                current_limit_left = None
                if rank2_limit is not None:
                    current_limit_left = max(0, rank2_limit - sum(1 for r in rows if r.rank == 2))

                parsed = parse_rank2_rows(t2p, drw_no, current_limit_left)

                if not parsed:
                    # ✅ "끝에 정보가 없으면 멈춤"
                    break

                added = 0
                for r in parsed:
                    key = (r.round, r.rank, r.store_name, r.address)
                    if key not in seen:
                        seen.add(key)
                        rows.append(r)
                        added += 1

                if added == 0:
                    # ✅ 새로 들어온 게 없으면 끝(중복 페이지/마지막 페이지)
                    break

                page += 1
                time.sleep(0.15)

            if page > rank2_max_pages:
                failures.append({"round": drw_no, "rank": 2, "page": rank2_max_pages, "reason": "Reached rank2_max_pages limit"})

    return rows, failures


# -----------------------------
# Build JSON
# -----------------------------
def build_json(range_n: int, include_rank2: bool, rank2_limit: Optional[int], rank2_max_pages: int) -> Dict:
    with requests.Session() as session:
        latest = get_latest_round(session)
        start = max(1, latest - range_n + 1)

        all_rows: List[StoreRow] = []
        failures: List[dict] = []

        stats = {
            "requestedRounds": range_n,
            "startRound": start,
            "endRound": latest,
            "parsedRounds": 0,
            "rank1Rows": 0,
            "rank2Rows": 0,
        }

        for drw_no in range(start, latest + 1):
            try:
                rows, fail = parse_round(
                    session=session,
                    drw_no=drw_no,
                    include_rank2=include_rank2,
                    rank2_limit=rank2_limit,
                    rank2_max_pages=rank2_max_pages,
                )
                failures.extend(fail)

                if rows:
                    stats["parsedRounds"] += 1
                    stats["rank1Rows"] += sum(1 for r in rows if r.rank == 1)
                    stats["rank2Rows"] += sum(1 for r in rows if r.rank == 2)

                all_rows.extend(rows)
                time.sleep(0.2)
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
                "rank2MaxPages": rank2_max_pages,
                "source": {
                    "topStoreGetUrlTemplate": TOPSTORE_GET_URL,
                    "topStoreBaseUrl": TOPSTORE_BASE_URL,
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
    parser.add_argument("--no-rank2", action="store_true", help="Exclude rank2 store list")

    # 2등 데이터가 너무 커지면 제한 권장 (0이면 제한 없음)
    parser.add_argument("--rank2-limit", type=int, default=int(os.getenv("WINNER_STORES_RANK2_LIMIT", "0")))
    # 2등 페이지네이션 안전 상한 (끝에 정보 없으면 자동 중단하지만, 최악 대비)
    parser.add_argument("--rank2-max-pages", type=int, default=int(os.getenv("WINNER_STORES_RANK2_MAX_PAGES", "80")))

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
        f"parsedRounds={s['parsedRounds']} rank1Rows={s['rank1Rows']} rank2Rows={s['rank2Rows']} "
        f"failures={len(data['meta']['failures'])}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
