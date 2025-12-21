#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Optional, List, Tuple

import requests

LOTTO_API_URL = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={drwNo}"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
}


def http_get_json(url: str, timeout: int = 20, retries: int = 4, backoff: float = 0.6) -> dict:
    last_exc = None
    for i in range(retries):
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_exc = e
            if i < retries - 1:
                time.sleep(backoff * (i + 1))
    raise RuntimeError(f"HTTP JSON GET failed: {url} ({last_exc})")


def lotto_api_success(drw_no: int) -> bool:
    data = http_get_json(LOTTO_API_URL.format(drwNo=drw_no))
    return data.get("returnValue") == "success"


def read_local_latest_round(data_files: List[str]) -> Optional[int]:
    for path in data_files:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            meta = data.get("meta") or {}
            v = meta.get("latestRound")
            if v is None:
                continue
            return int(v)
        except Exception:
            continue
    return None


def find_latest_round_by_api(hint: Optional[int] = None) -> int:
    """
    안정적인 최신 회차 탐지:
    - hint(로컬 latestRound)가 있으면 그 근처에서 빠르게 upper bound를 찾고 이진탐색
    - hint가 없으면 1..2000(필요시 확장) 범위를 이용해 upper bound 찾고 이진탐색
    """
    if hint is None:
        # 1) 기본 상한부터 시작
        lo = 1
        hi = 2000
        # hi가 여전히 success면 상한 확장
        while lotto_api_success(hi):
            lo = hi
            hi *= 2
            if hi > 10000:
                break
        # 2) 이진탐색: lo는 success, hi는 fail(또는 매우 큰 값)
        left, right = lo, hi
        while left + 1 < right:
            mid = (left + right) // 2
            if lotto_api_success(mid):
                left = mid
            else:
                right = mid
        return left

    # hint 기반 탐색
    # hint가 성공이 아닐 수도 있으니 보정
    if not lotto_api_success(hint):
        # hint 아래에서 마지막 success 찾기
        left, right = 1, max(2, hint)
        while left + 1 < right:
            mid = (left + right) // 2
            if lotto_api_success(mid):
                left = mid
            else:
                right = mid
        return left

    # hint는 success. upper bound를 빠르게 찾는다(지수 확장)
    last_success = hint
    step = 1
    probe = hint + step
    # 상한을 찾기 위해 step을 2배씩 늘리며 실패 지점 탐색
    while lotto_api_success(probe):
        last_success = probe
        step *= 2
        probe = hint + step
        if probe > 10000:
            break

    first_fail = probe

    # last_success..first_fail 사이 이진탐색으로 최신 회차 확정
    left, right = last_success, first_fail
    while left + 1 < right:
        mid = (left + right) // 2
        if lotto_api_success(mid):
            left = mid
        else:
            right = mid
    return left


def write_github_output(needs_update: bool, latest_remote: int, latest_local: Optional[int]) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        return
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(f"needs_update={'true' if needs_update else 'false'}\n")
        f.write(f"latest_remote={latest_remote}\n")
        f.write(f"latest_local={'' if latest_local is None else latest_local}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-files",
        nargs="*",
        default=[
            "data/region_1to2.json",
            "data/prize_2to5.json",
            "data/heatmap.json",
            "data/winner_stores.json",
        ],
        help="Local JSON files to check meta.latestRound (first valid wins).",
    )
    args = parser.parse_args()

    latest_local = read_local_latest_round(args.data_files)

    # ✅ 최신회차는 HTML이 아니라 API로 조회(안정)
    latest_remote = find_latest_round_by_api(hint=latest_local)

    # 로컬 파일이 없으면 초기 세팅이므로 업데이트
    needs_update = True if latest_local is None else (latest_remote != latest_local)

    write_github_output(needs_update, latest_remote, latest_local)

    print(f"[GUARD] latest_remote={latest_remote} latest_local={latest_local} needs_update={needs_update}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
