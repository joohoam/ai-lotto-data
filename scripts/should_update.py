#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Optional, List

import requests


BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
}


def http_get_text(url: str, timeout: int = 20) -> str:
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    resp.raise_for_status()

    # 인코딩 보정
    enc = (resp.encoding or "").lower().strip()
    if not enc or enc in ("iso-8859-1", "latin-1"):
        resp.encoding = resp.apparent_encoding or "euc-kr"
    return resp.text


def get_latest_round_from_bywin() -> int:
    html = http_get_text(BYWIN_URL)

    # 패턴들 순차 탐지
    m = re.search(r"lottoDrwNo\s*=\s*(\d+)", html)
    if m:
        return int(m.group(1))

    m = re.search(r'id=["\']lottoDrwNo["\'][^>]*value=["\'](\d+)["\']', html)
    if m:
        return int(m.group(1))

    m = re.search(r"(\d+)\s*회\s*당첨결과", html)
    if m:
        return int(m.group(1))

    raise RuntimeError("Could not parse latest round from byWin page.")


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


def write_github_output(needs_update: bool, latest_remote: int, latest_local: Optional[int]) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        return

    lines = [
        f"needs_update={'true' if needs_update else 'false'}",
        f"latest_remote={latest_remote}",
        f"latest_local={'' if latest_local is None else latest_local}",
    ]
    with open(out_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


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

    latest_remote = get_latest_round_from_bywin()
    latest_local = read_local_latest_round(args.data_files)

    # 로컬 파일이 없으면(초기 세팅) 무조건 업데이트
    if latest_local is None:
        needs_update = True
    else:
        needs_update = (latest_remote != latest_local)

    # Actions output
    write_github_output(needs_update, latest_remote, latest_local)

    # 로그
    print(f"[GUARD] latest_remote={latest_remote} latest_local={latest_local} needs_update={needs_update}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
