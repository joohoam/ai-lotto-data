#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Optional, List
import cloudscraper  # requirements.txt에 추가 필수

# 동행복권 메인 당첨결과 페이지 (API 대신 HTML 크롤링 사용)
BYWIN_URL = "https://dhlottery.co.kr/gameResult.do?method=byWin"

def get_latest_round_from_html() -> int:
    """
    cloudscraper를 사용하여 보안 페이지(WAF)를 우회하고 최신 회차를 가져옵니다.
    """
    # [핵심] 일반 requests 대신 cloudscraper 사용
    scraper = cloudscraper.create_scraper()
    
    try:
        # 봇 차단 우회 시도
        resp = scraper.get(BYWIN_URL, timeout=30)
        resp.raise_for_status()
        
        # 인코딩 처리
        if resp.encoding is None or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding
            
        html = resp.text
        
    except Exception as e:
        print(f"[ERROR] Cloudscraper failed: {e}")
        raise RuntimeError(f"Failed to fetch page: {e}")

    # 다양한 패턴으로 회차 번호 추출
    patterns = [
        r'<option[^>]*value=["\'](\d+)["\'][^>]*selected',  # 드롭박스 선택된 값
        r'id=["\']drwNo["\'][^>]*value=["\'](\d+)["\']',     # hidden input
