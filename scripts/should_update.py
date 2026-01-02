#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Optional, List

import requests

LOTTO_API_URL = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={drwNo}"

# [수정 1] 봇 차단 방지를 위해 헤더를 윈도우 크롬으로 변경하고 Referer 추가
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
    "Referer": "https://www.dhlottery.co.kr/",
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
            # [수정 2] 에러 발생 시 서버가 반환한 텍스트를 출력하여 디버깅 (JSON 파싱 실패 원인 확인용)
            if i == retries - 1: # 마지막 시도에서도 실패하면
                try:
                    print(f"[DEBUG] Failed Response Text (First 500 chars): {resp.text[:500]}")
                except:
                    pass
            
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
    if hint is None:
        lo = 1
        hi = 2000
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

    if not lotto_api_success(hint):
        left, right = 1, max(2, hint)
        while left + 1 < right:
            mid = (left + right) // 2
            if lotto_api_success(mid):
                left = mid
            else:
                right = mid
        return left

    last_success = hint
    step = 1
    probe = hint + step
    while lotto_api_success(probe):
        last_success = probe
        step *= 2
        probe = hint + step
        if probe > 10000:
            break

    first_fail = probe
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


def is_force_update() -> bool:
    v = (os.getenv("FORCE_UPDATE") or "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


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
    latest_remote = find_latest_round_by_api(hint=latest_local)

    if is_force_update():
        needs_update = True
    else:
        needs_update = True if latest_local is None else (latest_remote != latest_local)

    write_github_output(needs_update, latest_remote, latest_local)
    print(
        f"[GUARD] latest_remote={latest_remote} latest_local={latest_local} "
        f"needs_update={needs_update} force={is_force_update()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
