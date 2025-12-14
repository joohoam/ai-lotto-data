ai-lotto-data

AI LOTTO TRACKER 앱에서 사용하는 **원격 JSON 데이터(번호 히트맵 / 2~5등 상금 정보 / 1~2등 당첨지역)**를 GitHub Actions로 자동 생성·갱신하는 데이터 저장소입니다.

데이터는 data/*.json에 생성됩니다.

앱에서는 GitHub Raw URL로 JSON을 fetch하여 사용합니다.

네트워크 실패 시에도 앱이 크래시하지 않도록 앱 측에서 캐싱을 권장합니다.

제공 데이터 (Outputs)
1) 번호 히트맵 (최근 N회)

파일: data/heatmap.json

용도: 최근 N회(기본 40회/설정값) 기준 1~45 번호 출현 빈도

Raw URL:

https://raw.githubusercontent.com/joohoam/ai-lotto-data/main/data/heatmap.json

2) 2~5등 상금 정보

파일: data/prize_2to5.json

용도: 회차별 2~5등 (총상금/당첨자수/1게임당 상금/기준)

Raw URL:

https://raw.githubusercontent.com/joohoam/ai-lotto-data/main/data/prize_2to5.json

3) 1~2등 당첨지역 (시/도 + 인터넷)

파일: data/region_1to2.json

용도: 회차별 1등/2등 판매점 지역 집계(시/도 + 인터넷/기타)

Raw URL:

https://raw.githubusercontent.com/joohoam/ai-lotto-data/main/data/region_1to2.json

폴더 구조
.
├─ .github/
│  └─ workflows/
│     └─ update_lotto_data.yml      # 자동 갱신 워크플로우
├─ scripts/
│  ├─ requirements.txt             # Python deps
│  ├─ update_heatmap.py            # heatmap.json 생성/갱신
│  ├─ update_prize_2to5.py         # prize_2to5.json 생성/갱신
│  └─ update_region_1to2.py        # region_1to2.json 생성/갱신
└─ data/
   ├─ heatmap.json
   ├─ prize_2to5.json
   └─ region_1to2.json

동작 방식

GitHub Actions가 정해진 스케줄에 따라 다음을 수행합니다.

Python 실행 환경 구성

scripts/requirements.txt 설치

scripts/update_*.py 실행 → data/*.json 갱신

변경 사항이 있으면 자동 commit & push

실행 주기 (Schedule)

워크플로우는 다음 시간에 실행되도록 설정되어 있습니다.

매주 토요일 21:05 KST

매주 토요일 21:30 KST

GitHub Actions cron은 UTC 기준입니다. KST(UTC+9) 시간을 UTC로 변환해 cron을 설정합니다.

로컬에서 수동 실행

Python 3.11+ 권장

pip install -r scripts/requirements.txt
python scripts/update_heatmap.py
python scripts/update_prize_2to5.py
python scripts/update_region_1to2.py

데이터 스키마 개요
heatmap.json

meta.latestRound: 최신 회차

meta.range: 최근 집계 회수(N회)

counts: "1" ~ "45" 번호별 출현 횟수

prize_2to5.json

최상위 키: 회차 문자열 "1202" 등

하위 키: "2", "3", "4", "5"

값: totalPrize, winners, perGamePrize, criteria

region_1to2.json

meta.latestRound, meta.range, meta.updatedAt

rounds[회차].rank1 / rank2

totalStores

bySido (서울~제주)

internet, other

Notes

데이터 소스는 동행복권 공개 페이지/응답을 기반으로 합니다.

사이트 구조 변경 등으로 파싱이 실패할 수 있으며, 이 경우 워크플로우 로그를 확인해 스크립트를 업데이트해야 합니다.


## License
No license. All rights reserved.
