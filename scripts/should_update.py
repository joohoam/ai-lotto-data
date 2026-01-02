#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Optional, List

import requests

# [변경] API를 여러번 호출하는 대신, 메인 결과 페이지를 1번 크롤링합니다.
BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin"

# 봇 차단 방지 헤더
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
    "Referer": "https://dhlottery.co.kr/",
}

def http_get_text(url: str, timeout: int = 20, retries: int = 3, backoff: float = 1.0) -> str:
    """HTML 텍스트를 가져오는 함수"""
    last_exc = None
    for i in range(retries):
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            resp.raise_for_status()
            
            # 인코딩 처리 (EUC-KR 대응)
            if resp.encoding is None or resp.encoding.lower() == 'iso-8859-1':
                resp.encoding = resp.apparent_encoding
                
            return resp.text
        except Exception as e:
            last_exc = e
            if i < retries - 1:
                time.sleep(backoff * (i + 1))
                
    # 에러 발생 시 로그 출력
    print(f"[ERROR] Failed to fetch {url}")
    raise RuntimeError(f"HTTP GET failed: {url} ({last_exc})")

def get_latest_round_from_html() -> int:
    """
    동행복권 결과 페이지(HTML)에서 최신 회차 번호를 파싱합니다.
    (API 이진 탐색보다 요청 횟수가 적어 차단 확률이 낮습니다)
    """
    html = http_get_text(BYWIN_URL)
    
    # 패턴 1: <option value="1204" selected>1204회</option>
    # 패턴 2: <input type="hidden" id="drwNo" value="1204">
    # 패턴 3: <h4><strong>1204회</strong> 당첨결과</h4>
    
    patterns = [
        r'<option[^>]*value=["\'](\d+)["\'][^>]*selected',  # 드롭박스 선택된 값
        r'id=["\']drwNo["\'][^>]*value=["\'](\d+)["\']',     # hidden input
        r'<strong>(\d+)회</strong>',                         # 제목
        r'value=["\'](\d+)["\']',                            # 단순 value (범용)
    ]
    
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return int(m.group(1))
            
    # 파싱 실패 시 디버깅을 위해 HTML 일부 출력
    print("[DEBUG] HTML Parsing Failed. Content start:")
    print(html[:500])
    raise RuntimeError("Failed to parse latest round from HTML")

def read_local_latest_round(data_files: List[str]) -> Optional[int]:
    """로컬 파일들에서 가장 최신 회차 정보를 읽어옵니다."""
    max_round = None
    for path in data_files:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 파일 구조에 따라 latestRound 위치가 다를 수 있음
            # 1. meta.latestRound
            meta = data.get("meta") or {}
            v = meta.get("latestRound")
            
            # 2. 만약 meta가 없고 rounds 키가 있다면 키값 중 최대값 확인
            if v is None and "rounds" in data:
                keys = [int(k) for k in data["rounds"].keys() if str(k).isdigit()]
                if keys:
                    v = max(keys)

            if v is not None:
                v = int(v)
                if max_round is None or v > max_round:
                    max_round = v
        except Exception:
            continue
    return max_round

def write_github_output(needs_update: bool, latest_remote: int, latest_local: Optional[int]) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        return
    
    print(f"[INFO] Writing GITHUB_OUTPUT: needs_update={needs_update}, remote={latest_remote}, local={latest_local}")
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
        help="Local JSON files to check current version.",
    )
    args = parser.parse_args()

    # 1. 로컬 데이터의 최신 회차 확인
    latest_local = read_local_latest_round(args.data_files)
    
    # 2. 원격(동행복권) 최신 회차 확인 (HTML 파싱 1회)
    try:
        latest_remote = get_latest_round_from_html()
    except Exception as e:
        print(f"[CRITICAL] Could not fetch remote round: {e}")
        # 오류가 나더라도 업데이트를 시도하지 않도록 안전하게 종료하거나,
        # 강제로 업데이트를 시도하게 할 수 있습니다. 여기서는 에러로 종료합니다.
        return 1

    # 3. 업데이트 필요 여부 결정
    if is_force_update():
        needs_update = True
        print("[GUARD] Force update enabled.")
    else:
        # 로컬이 없거나, 로컬 버전이 원격보다 낮으면 업데이트
        if latest_local is None:
            needs_update = True
            print("[GUARD] No local data found. Update needed.")
        elif latest_remote > latest_local:
            needs_update = True
            print(f"[GUARD] Update needed: remote({latest_remote}) > local({latest_local})")
        else:
            needs_update = False
            print(f"[GUARD] Up to date: remote({latest_remote}) == local({latest_local})")

    # 4. 결과 출력
    write_github_output(needs_update, latest_remote, latest_local)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
