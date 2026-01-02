#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Optional, List
import requests
import cloudscraper

# 동행복권 URL
OFFICIAL_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin"
# 네이버 로또 검색 URL (차단 우회용 백업)
NAVER_URL = "https://search.naver.com/search.naver?where=nexearch&sm=top_hty&fbm=0&ie=utf8&query=%EB%A1%9C%EB%98%90"

def get_latest_round_from_naver() -> int:
    """
    동행복권 사이트가 차단될 경우, 네이버 검색 결과에서 회차 정보를 가져옵니다.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(NAVER_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        html = resp.text
        
        # 네이버 검색 결과 파싱 (예: "_lotto-btn-current">1204회</a>)
        # 다양한 패턴 대응
        patterns = [
            r'class=["\'].*?_lotto-btn-current.*?["\']>(\d+)회',
            r'(\d+)회 당첨결과',
            r'<strong>(\d+)회</strong>'
        ]
        
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                print(f"[INFO] Fetched latest round from NAVER: {m.group(1)}")
                return int(m.group(1))
                
    except Exception as e:
        print(f"[WARN] Naver fetch failed: {e}")
    
    raise RuntimeError("All sources (Official & Naver) failed to fetch latest round.")

def get_latest_round_remote() -> int:
    """
    1순위: 동행복권 공식 (Cloudscraper)
    2순위: 네이버 검색 (Requests)
    """
    # 1. 공식 사이트 시도
    try:
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(OFFICIAL_URL, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            # 보안 페이지(rsaModulus)가 아닌 실제 콘텐츠인지 확인
            if "rsaModulus" not in html:
                patterns = [
                    r'<option[^>]*value=["\'](\d+)["\'][^>]*selected',
                    r'<strong>(\d+)회</strong>'
                ]
                for pat in patterns:
                    m = re.search(pat, html)
                    if m:
                        return int(m.group(1))
    except Exception as e:
        print(f"[WARN] Official site fetch failed: {e}")

    # 2. 실패 시 네이버 시도
    print("[INFO] Switching to Naver fallback...")
    return get_latest_round_from_naver()

def read_local_latest_round(data_files: List[str]) -> Optional[int]:
    max_round = None
    for path in data_files:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            v = data.get("meta", {}).get("latestRound")
            
            # 메타가 없으면 라운드 키값 중 최대값 검색
            if v is None and "rounds" in data:
                keys = [int(k) for k in data["rounds"].keys() if str(k).isdigit()]
                if keys:
                    v = max(keys)

            if v is not None:
                if max_round is None or int(v) > max_round:
                    max_round = int(v)
        except:
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

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-files", nargs="*", default=[])
    args = parser.parse_args()

    latest_local = read_local_latest_round(args.data_files)
    
    try:
        latest_remote = get_latest_round_remote()
    except Exception as e:
        print(f"[CRITICAL] {e}")
        return 1

    needs_update = True
    if latest_local and latest_remote <= latest_local:
        if not (os.getenv("FORCE_UPDATE") or "").strip().lower() in ("1", "true", "yes", "on"):
            needs_update = False

    print(f"[GUARD] Local={latest_local}, Remote={latest_remote}, Update={needs_update}")
    write_github_output(needs_update, latest_remote, latest_local)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
