# ai-lotto-data

동행복권(로또 6/45) 공개 데이터 및 웹 페이지 정보를 기반으로, 앱(예: AI LOTTO TRACKER)에서 사용할 수 있는 JSON 데이터를 자동 생성/갱신하는 리포지토리입니다.

- GitHub Actions 스케줄 실행으로 최신 데이터를 수집합니다.
- 생성된 JSON은 `raw.githubusercontent.com` 주소로 앱에서 직접 로딩할 수 있습니다.

---

## Output JSON (data/)

| File | Description |
|---|---|
| `data/heatmap.json` | 번호 히트맵/빈도 분석용 데이터 |
| `data/prize_2to5.json` | 2~5등 당첨금/당첨자 수(회차별) |
| `data/region_1to2.json` | 지역별 1~2등 당첨자(판매점) 집계 데이터 |
| `data/winner_stores.json` | 1등 배출점 상세(상호/자동·수동/주소) 목록 (최근 N회) |

---

## Raw JSON URLs (for app)

아래 URL을 앱에서 HTTP로 로드하세요.

```txt
https://raw.githubusercontent.com/joohoam/ai-lotto-data/main/data/heatmap.json
https://raw.githubusercontent.com/joohoam/ai-lotto-data/main/data/prize_2to5.json
https://raw.githubusercontent.com/joohoam/ai-lotto-data/main/data/region_1to2.json
https://raw.githubusercontent.com/joohoam/ai-lotto-data/main/data/winner_stores.json
How it works
GitHub Actions가 스케줄에 따라 실행됩니다.

scripts/*.py가 동행복권 API/페이지를 통해 데이터를 수집합니다.

결과물을 data/*.json으로 저장하고 변경사항이 있으면 커밋/푸시합니다.

Scripts (scripts/)
Script	Purpose
scripts/update_heatmap.py	히트맵 데이터 갱신
scripts/update_prize_2to5.py	2~5등 데이터 갱신
scripts/update_region_1to2.py	지역별 1~2등 집계 갱신
scripts/update_winner_stores.py	1등 배출점(상세) 크롤링/정규화 갱신

Install dependencies
bash
코드 복사
pip install -r scripts/requirements.txt
Run locally
bash
코드 복사
# heatmap
python scripts/update_heatmap.py

# prize (2~5)
python scripts/update_prize_2to5.py

# region (1~2) - 최근 N회는 env로 제어
REGION_RANGE=10 python scripts/update_region_1to2.py

# winner stores (1등 배출점 상세) - 최근 N회
python scripts/update_winner_stores.py --range 10 --out data/winner_stores.json
winner_stores.json schema (summary)
meta

updatedAt: UTC ISO8601

latestRound: 최신 회차

range: 최근 몇 회차 수집했는지

failures: 특정 회차 파싱 실패 기록(디버깅용)

byRegion

"서울", "경기", "부산" 등 지역 키 → 배출점 배열

byRound

"1203" 같은 회차 키 → 배출점 배열

Disclaimer
본 데이터는 공개 정보를 기반으로 가공/제공됩니다.

동행복권 사이트/공개 API 정책 및 제공 형식 변경에 따라 수집/구조가 바뀔 수 있습니다.

본 데이터는 참고용이며, 당첨을 보장하지 않습니다.

asciidoc
코드 복사
::contentReference[oaicite:0]{index=0}
