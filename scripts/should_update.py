#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Optional, List
import cloudscraper

# 동행복권 메인 당첨결과 페이지
BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin"

def get_latest_round_from_html() -> int:
    # cloudscraper로 WAF(보안) 우회
    scraper = cloudscraper.create_scraper()
    try:
        resp = scraper.get(BYWIN_URL, timeout=30)
        resp.raise_for_status()
        
        if resp.encoding is None or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding
            
        html = resp.text
        
    except Exception as e:
        print(f"[ERROR] Cloudscraper failed: {e}")
        raise RuntimeError(f"Failed to fetch page: {e}")

    patterns = [
        r'<option[^>]*value=["\'](\d+)["\'][^>]*selected',
        r'id=["\']drwNo["\'][^>]*value=["\'](\d+)["\']',
        r'<strong>(\d+)회</strong>',
        r'value=["\'](\d+)["\']'
    ]
    
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return int(m.group(1))
            
    print("[DEBUG] HTML Parsing Failed. Content start:")
    print(html[:500])
    raise RuntimeError("Failed to parse latest round from HTML")

def read_local_latest_round(data_files: List[str]) -> Optional[int]:
    max_round = None
    for path in data_files:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            meta = data.get("meta") or {}
            v = meta.get("latestRound")
            
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

    latest_local = read_local_latest_round(args.data_files)
    
    try:
        latest_remote = get_latest_round_from_html()
    except Exception as e:
        print(f"[CRITICAL] Could not fetch remote round: {e}")
        return 1

    if is_force_update():
        needs_update = True
    else:
        if latest_local is None:
            needs_update = True
        elif latest_remote > latest_local:
            needs_update = True
        else:
            needs_update = False

    print(f"[GUARD] Local={latest_local}, Remote={latest_remote}, Update={needs_update}")
    write_github_output(needs_update, latest_remote, latest_local)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
