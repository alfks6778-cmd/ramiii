# gigs_competitor_crawling

원티드긱스 경쟁사(위시켓, 이랜서, 크몽엔터프라이즈) 프로젝트 공고 크롤링 자동화

## 구조

```
gigs_competitor_crawling/
├── .github/workflows/gigs-crawl.yml   # GitHub Actions 워크플로
├── crawlers/
│   ├── elancer_crawler.py             # 이랜서 — API 기반 (requests)
│   ├── wishket_crawler.py             # 위시켓 — Playwright 브라우저
│   └── kmong_crawler.py               # 크몽 — Playwright 브라우저
├── pipeline/
│   └── gsheet_pipeline.py             # CSV 병합 → Drive/Sheet 업로드
├── config.json
├── requirements.txt
└── README.md
```

## 실행 흐름

1. GitHub Actions가 매주 월요일 09:00 KST 자동 실행
2. 3개 크롤러가 순차 실행 → 플랫폼별 CSV 생성
3. Pipeline이 CSV 병합 → Google Sheet 업데이트
4. CSV + 디버그 파일이 Artifact로 보관 (30일)

## 수집 컬럼 (11열)

| No. | 플랫폼 | 프로젝트 제목 | 등록일 | 금액 | 예상기간 | 기간제/외주 | 직무 | 스킬 | 근무지 | 수집일시 |

## GitHub Secrets 설정

| Secret | 설명 |
|---|---|
| `GCP_SA_JSON` | Google 서비스계정 JSON (전체 내용) |
| `SPREADSHEET_ID` | 대상 Google Spreadsheet ID |
| `SHEET_GID` | 대상 시트의 GID |
| `DRIVE_FOLDER_ID` | CSV 업로드할 Drive 폴더 ID (선택) |

## 최초 세팅

1. GitHub 레포 생성 후 코드 push
2. Settings → Secrets and variables → Actions → 위 4개 Secret 등록
3. 서비스계정 이메일을 대상 Spreadsheet에 편집자로 공유
4. Actions 탭 → "Gigs Competitor → CSV → Sheet" → Run workflow 수동 실행
5. Artifact와 Google Sheet 결과 확인

## 수동 실행

Actions 탭에서 Run workflow 시 개별 플랫폼 스킵 가능:
- `skip_wishket`: 위시켓 건너뛰기
- `skip_elancer`: 이랜서 건너뛰기
- `skip_kmong`: 크몽 건너뛰기

## 로컬 테스트

```bash
pip install -r requirements.txt
playwright install chromium

# 개별 크롤러 실행
python crawlers/elancer_crawler.py
python crawlers/wishket_crawler.py
python crawlers/kmong_crawler.py

# 파이프라인 (Google 인증 필요)
export SA_JSON_PATH=path/to/sa.json
export SPREADSHEET_ID=your_spreadsheet_id
export SHEET_GID=your_sheet_gid
python pipeline/gsheet_pipeline.py
```
