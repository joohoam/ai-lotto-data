#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List

# [핵심] 서버 접속 없이 날짜로 회차 계산 (차단 원천 봉쇄)
# 기준: 1152회차 = 2024년 12월 28일 토요일
ANCHOR_ROUND = 1152
ANCHOR_DATE = datetime(2024, 12, 28, 20, 0, 0, tzinfo=timezone(timedelta(hours=9))) # KST 기준

def get_latest_round_by_date() -> int:
    """
    오늘 날짜를 기준으로 최신 회차를 수학적으로 계산합니다.
    네트워크 요청을 보내지 않으므로 오류가 날 수 없습니다.
    """
    # 현재 한국 시간(KST)
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    
    # 기준일로부터 지난 주(week) 수 계산
    diff = now_kst - ANCHOR_DATE
    weeks_passed = diff.days // 7
    
    # 예상 회차
    estimated_round = ANCHOR_ROUND + weeks_passed
    
    # 예외 처리: 오늘이 토요일(weekday 5)인데 21시 전이라면 아직 추첨 전임
    # (월=0, ... 토=5, 일=6)
    if now_kst.weekday() == 5:
        if now_kst.hour < 21:
            estimated_round -= 1
            
    return estimated_round

def read_local_latest_round(data_files: List[str]) -> Optional[int]:
    max_round = None
    for path in data_files:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            v = data.get("meta", {}).get("latestRound")
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
    
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(f"needs_update={'true' if needs_update else 'false'}\n")
        f.write(f"latest_remote={latest_remote}\n")
        f.write(f"latest_local={'' if latest_local is None else latest_local}\n")

def is_force_update() -> bool:
    v = (os.getenv("FORCE_UPDATE") or "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-files", nargs="*", default=[])
    args = parser.parse_args()

    # 1. 로컬 데이터 버전 확인
    latest_local = read_local_latest_round(args.data_files)
    
    # 2. 최신 회차 확인 (날짜 계산 방식)
    latest_remote = get_latest_round_by_date()
    
    # 3. 업데이트 여부 결정
    needs_update = False
    if is_force_update():
        needs_update = True
        print("[GUARD] Force update enabled.")
    elif latest_local is None:
        needs_update = True
        print(f"[GUARD] No local data. Update needed (Target: {latest_remote}).")
    elif latest_remote > latest_local:
        needs_update = True
        print(f"[GUARD] Update needed: Remote({latest_remote}) > Local({latest_local})")
    else:
        print(f"[GUARD] Up to date: Remote({latest_remote}) == Local({latest_local})")

    write_github_output(needs_update, latest_remote, latest_local)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
