#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List

# [핵심] 서버에 요청하지 않고, 날짜 기준으로 회차를 계산합니다.
# 기준일: 1152회차 = 2024년 12월 28일 (토)
ANCHOR_ROUND = 1152
ANCHOR_DATE = datetime(2024, 12, 28, 20, 0, 0, tzinfo=timezone(timedelta(hours=9))) # KST 기준

def get_latest_round_by_date() -> int:
    """
    오늘 날짜를 기준으로 최신 회차를 계산합니다.
    (네트워크 요청 X -> 차단/오류 발생 0%)
    """
    # 현재 한국 시간(KST) 구하기
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    
    # 기준일로부터 며칠 지났는지 계산
    diff = now_kst - ANCHOR_DATE
    weeks_passed = diff.days // 7
    
    # 예상 회차
    estimated_round = ANCHOR_ROUND + weeks_passed
    
    # 예외 처리: 오늘이 토요일(추첨일)인데 아직 추첨 시간(21:00) 전이라면
    # 최신 회차는 아직 나오지 않았으므로 1을 뺍니다.
    # (weekday: 월=0, ..., 토=5, 일=6)
    if now_kst.weekday() == 5:
        if now_kst.hour < 21:
            estimated_round -= 1
            
    return estimated_round

def read_local_latest_round(data_files: List[str]) -> Optional[int]:
    """로컬 파일에서 현재 저장된 최신 회차를 읽습니다."""
    max_round = None
    for path in data_files:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # meta.latestRound 확인
            v = data.get("meta", {}).get("latestRound")
            
            # 없으면 rounds 키들 중 최댓값 확인
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
    
    # 2. 원격 버전 확인 (날짜 계산)
    latest_remote = get_latest_round_by_date()
    
    # 3. 업데이트 필요 여부 결정
    if is_force_update():
        needs_update = True
        print("[GUARD] Force update enabled.")
    else:
        if latest_local is None:
            needs_update = True
            print(f"[GUARD] No local data. Update needed (Target: {latest_remote}).")
        elif latest_remote > latest_local:
            needs_update = True
            print(f"[GUARD] Update needed: Remote({latest_remote}) > Local({latest_local})")
        else:
            needs_update = False
            print(f"[GUARD] Up to date: Remote({latest_remote}) == Local({latest_local})")

    write_github_output(needs_update, latest_remote, latest_local)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
