"""
Google Sheet Pipeline — CSV 병합 → Google Drive 업로드 → 시트 반영
PRD 표준 패턴: Crawler → CSV → Drive Upload → Sheet Pipeline
"""

import csv
import json
import os
import sys
import glob
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ── 설정 ────────────────────────────────────────────────
OUT_DIR = os.environ.get("OUT_DIR", "out")
SA_JSON_PATH = os.environ.get("SA_JSON_PATH", "sa.json")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS_ROW = [
    "No.", "플랫폼", "프로젝트 제목", "등록일", "금액",
    "예상기간", "기간제/외주", "직무", "스킬", "근무지", "수집일시",
]

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_GID = int(os.environ.get("SHEET_GID", "0"))
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")


def get_credentials() -> Credentials:
    if not os.path.exists(SA_JSON_PATH):
        raise FileNotFoundError(
            f"서비스 계정 JSON 파일을 찾을 수 없습니다: {SA_JSON_PATH}"
        )
    return Credentials.from_service_account_file(SA_JSON_PATH, scopes=SCOPES)


def merge_csvs() -> tuple[list[list], dict[str, int]]:
    csv_files = sorted(glob.glob(os.path.join(OUT_DIR, "*.csv")))
    if not csv_files:
        print("[pipeline] CSV 파일 없음")
        return [], {}

    all_rows: list[list] = []
    platform_counts: dict[str, int] = {}

    for csv_path in csv_files:
        print(f"[pipeline] 읽기: {csv_path}")
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            rows = list(reader)
            for row in rows:
                if len(row) >= 2:
                    platform = row[1]
                    platform_counts[platform] = platform_counts.get(platform, 0) + 1
            all_rows.extend(rows)

    for i, row in enumerate(all_rows):
        if row:
            row[0] = str(i + 1)

    print(f"[pipeline] 총 {len(all_rows)}건 병합 완료")
    for plat, cnt in platform_counts.items():
        print(f"  - {plat}: {cnt}건")

    return all_rows, platform_counts


def upload_to_drive(merged_csv_path: str, creds: Credentials) -> str | None:
    if not DRIVE_FOLDER_ID:
        print("[pipeline] DRIVE_FOLDER_ID 없음 → Drive 업로드 스킵")
        return None

    service = build("drive", "v3", credentials=creds)
    today = datetime.now().strftime("%y%m%d")
    file_name = f"gigs_competitor_{today}.csv"

    file_metadata = {"name": file_name, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(merged_csv_path, mimetype="text/csv")
    uploaded = service.files().create(
        body=file_metadata, media_body=media, fields="id"
    ).execute()

    file_id = uploaded.get("id")
    print(f"[pipeline] Drive 업로드 완료: {file_name} (id: {file_id})")
    return file_id


def write_to_sheet(all_rows: list[list], creds: Credentials):
    if not SPREADSHEET_ID:
        print("[pipeline] SPREADSHEET_ID 없음 → 시트 쓰기 스킵")
        return

    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)

    sheet = None
    for ws in spreadsheet.worksheets():
        if ws.id == SHEET_GID:
            sheet = ws
            break

    if not sheet:
        print(f"[pipeline] GID {SHEET_GID} 시트를 찾을 수 없음 → 첫 번째 시트 사용")
        sheet = spreadsheet.sheet1

    sheet.clear()
    all_data = [HEADERS_ROW] + all_rows

    if all_data:
        sheet.update(range_name=f"A1:K{len(all_data)}", values=all_data)

    print(f"[pipeline] 시트 업데이트 완료: {len(all_rows)}건")


def save_merged_csv(all_rows: list[list]) -> str:
    today = datetime.now().strftime("%y%m%d")
    merged_path = os.path.join(OUT_DIR, f"gigs_merged_{today}.csv")
    with open(merged_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS_ROW)
        writer.writerows(all_rows)
    print(f"[pipeline] 병합 CSV 저장: {merged_path}")
    return merged_path


def run():
    print("=" * 50)
    print("[pipeline] 시작")
    print("=" * 50)

    all_rows, counts = merge_csvs()
    if not all_rows:
        print("[pipeline] 수집 데이터 없음 → 종료")
        sys.exit(1)

    merged_path = save_merged_csv(all_rows)

    try:
        creds = get_credentials()
    except FileNotFoundError as e:
        print(f"[pipeline] 인증 실패: {e}")
        print(f"[pipeline] 로컬 CSV는 사용 가능: {merged_path}")
        return

    upload_to_drive(merged_path, creds)
    write_to_sheet(all_rows, creds)

    print("=" * 50)
    print("[pipeline] 완료")
    for plat, cnt in counts.items():
        print(f"  {plat}: {cnt}건")
    print(f"  합계: {len(all_rows)}건")
    print("=" * 50)


if __name__ == "__main__":
    run()
