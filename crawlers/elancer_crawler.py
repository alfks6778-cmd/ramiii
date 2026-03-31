"""
이랜서(Elancer) 크롤러 — API 기반
PRD 표준: CSV 출력 → pipeline에서 Google Sheet 반영
"""

import csv
import json
import os
import sys
import math
import requests
from datetime import datetime

# ── 설정 ────────────────────────────────────────────────
API_URL = "https://api.elancer.co.kr/api/pjt/get_list"
PAGE_SIZE = 20
MAX_PAGES = 50  # 안전 가드

HEADERS_ROW = [
    "No.", "플랫폼", "프로젝트 제목", "등록일", "금액",
    "예상기간", "기간제/외주", "직무", "스킬", "근무지", "수집일시",
]

OUT_DIR = os.environ.get("OUT_DIR", "out")
TODAY = datetime.now().strftime("%y%m%d")
CSV_PATH = os.path.join(OUT_DIR, f"elancer_{TODAY}.csv")


# ── 직무 매핑 ───────────────────────────────────────────
DUTY_MAP = {
    "1": "웹개발", "2": "앱개발", "3": "퍼블리싱",
    "4": "디자인", "5": "기획", "6": "데이터",
    "7": "인프라", "8": "QA/테스트", "9": "PM/PMO",
    "10": "기타",
}


def fetch_page(page: int) -> dict:
    """이랜서 API 한 페이지 조회"""
    payload = {
        "pgNo": page,
        "pgSize": PAGE_SIZE,
        "sortBy": "latest",
        "pjtState": "R",      # 모집중
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://www.elancer.co.kr",
        "Referer": "https://www.elancer.co.kr/",
    }
    resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_projects(data: dict) -> list[dict]:
    """API 응답에서 프로젝트 리스트 파싱"""
    items = data.get("data", {}).get("pjtList") or data.get("data", {}).get("list") or []
    if not items and isinstance(data.get("data"), list):
        items = data["data"]
    return items


def extract_row(item: dict, idx: int, collect_time: str) -> list:
    """프로젝트 한 건 → 행 변환"""
    # 직무 추출
    duty_cd = str(item.get("dutyCd", "") or "")
    job_role = DUTY_MAP.get(duty_cd, item.get("dutyNm", "") or "")

    # 스킬 추출
    skills_raw = item.get("skillNm") or item.get("langNm") or ""
    if isinstance(skills_raw, list):
        skills = ", ".join(str(s) for s in skills_raw)
    else:
        skills = str(skills_raw)

    # 금액
    amt_start = item.get("pjtAmtStart") or item.get("monthAmt") or ""
    amt_end = item.get("pjtAmtEnd") or ""
    if amt_start and amt_end and str(amt_start) != str(amt_end):
        amount = f"{amt_start}~{amt_end}만원"
    elif amt_start:
        amount = f"{amt_start}만원"
    else:
        amount = "협의"

    # 기간
    duration_raw = item.get("pjtDuration") or item.get("expectMonth") or ""
    duration = f"{duration_raw}개월" if duration_raw else "미정"

    # 기간제/외주
    pjt_type = item.get("pjtTypeNm") or item.get("workType") or ""

    # 근무지
    location = item.get("workPlace") or item.get("addr") or ""

    return [
        idx,
        "이랜서",
        item.get("pjtName") or item.get("title") or "",
        item.get("regDt") or item.get("regDate") or "",
        amount,
        duration,
        pjt_type,
        job_role,
        skills,
        location,
        collect_time,
    ]


def crawl() -> str:
    """전체 수집 후 CSV 저장, CSV 경로 반환"""
    os.makedirs(OUT_DIR, exist_ok=True)
    collect_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    all_rows: list[list] = []

    print("[elancer] 수집 시작")

    for page in range(1, MAX_PAGES + 1):
        try:
            data = fetch_page(page)
        except Exception as e:
            print(f"[elancer] page {page} 요청 실패: {e}")
            break

        items = parse_projects(data)
        if not items:
            print(f"[elancer] page {page}: 데이터 없음 → 종료")
            break

        for item in items:
            row = extract_row(item, len(all_rows) + 1, collect_time)
            all_rows.append(row)

        total = data.get("data", {}).get("totalCnt") or data.get("data", {}).get("total") or 0
        total_pages = math.ceil(int(total) / PAGE_SIZE) if total else page
        print(f"[elancer] page {page}/{total_pages}  누적 {len(all_rows)}건")

        if page >= total_pages:
            break

    # CSV 쓰기
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS_ROW)
        writer.writerows(all_rows)

    print(f"[elancer] 완료: {len(all_rows)}건 → {CSV_PATH}")
    return CSV_PATH


if __name__ == "__main__":
    path = crawl()
    print(f"CSV saved: {path}")
