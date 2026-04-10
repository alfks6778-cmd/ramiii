"""
웹훅 파이프라인 — CSV 병합 후 Apps Script 웹 앱으로 POST 전송
서비스 계정 불필요. GitHub Secret에 WEBHOOK_URL과 SHARED_SECRET만 있으면 됨.
"""

import csv
import json
import os
import sys
import glob
import time
import urllib.request
import urllib.error
from datetime import datetime

# ── 설정 ────────────────────────────────────────────────
OUT_DIR = os.environ.get("OUT_DIR", "out")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
SHARED_SECRET = os.environ.get("SHARED_SECRET", "")

HEADERS_ROW = [
    "No.", "플랫폼", "프로젝트 제목", "등록일", "금액",
    "예상기간", "기간제/외주", "직무", "스킬", "근무지", "수집일시",
]


def merge_csvs() -> tuple[list[list], dict[str, int]]:
    """out/ 폴더의 모든 CSV를 병합"""
    csv_files = sorted(glob.glob(os.path.join(OUT_DIR, "*.csv")))
    csv_files = [f for f in csv_files if "debug" not in f and "gigs_merged" not in f]

    if not csv_files:
        print("[pipeline] CSV 파일 없음")
        return [], {}

    all_rows: list[list] = []
    platform_counts: dict[str, int] = {}

    for csv_path in csv_files:
        print(f"[pipeline] 읽기: {csv_path}")
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    platform = row[1]
                    platform_counts[platform] = platform_counts.get(platform, 0) + 1
                all_rows.append(row)

    for i, row in enumerate(all_rows):
        if row:
            row[0] = str(i + 1)

    print(f"[pipeline] 총 {len(all_rows)}건 병합")
    for plat, cnt in platform_counts.items():
        print(f"  - {plat}: {cnt}건")

    return all_rows, platform_counts


def save_merged_csv(all_rows: list[list]) -> str:
    today = datetime.now().strftime("%y%m%d")
    merged_path = os.path.join(OUT_DIR, f"gigs_merged_{today}.csv")
    with open(merged_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS_ROW)
        writer.writerows(all_rows)
    print(f"[pipeline] 병합 CSV 저장: {merged_path}")
    return merged_path


def post_to_webhook(rows: list[list]) -> dict:
    """Apps Script 웹훅으로 데이터 전송"""
    if not WEBHOOK_URL:
        raise ValueError("WEBHOOK_URL 환경변수 없음")
    if not SHARED_SECRET:
        raise ValueError("SHARED_SECRET 환경변수 없음")

    payload = {
        "secret": SHARED_SECRET,
        "rows": rows,
        "timestamp": datetime.now().isoformat(),
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "gigs-crawler-pipeline/1.0",
        },
        method="POST",
    )

    last_err = None
    for attempt in range(3):
        try:
            print(f"[pipeline] 웹훅 전송 시도 {attempt + 1}/3 ({len(rows)}건)")
            with urllib.request.urlopen(req, timeout=300) as resp:
                status = resp.status
                body = resp.read().decode("utf-8")
                print(f"[pipeline] 응답 {status}: {body[:500]}")
                if status == 200:
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError:
                        return {"ok": True, "raw": body}
                else:
                    last_err = f"HTTP {status}: {body}"
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
            last_err = f"HTTPError {e.code}: {body}"
            print(f"[pipeline] {last_err}")
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print(f"[pipeline] {last_err}")

        if attempt < 2:
            time.sleep(5)

    raise RuntimeError(f"웹훅 전송 실패 (3회 재시도): {last_err}")


def run():
    print("=" * 50)
    print("[pipeline] 웹훅 파이프라인 시작")
    print("=" * 50)

    all_rows, counts = merge_csvs()
    if not all_rows:
        print("[pipeline] 수집 데이터 없음")
        sys.exit(1)

    save_merged_csv(all_rows)

    result = post_to_webhook(all_rows)

    print("=" * 50)
    print("[pipeline] 완료")
    print(f"  전송 건수: {len(all_rows)}")
    print(f"  응답: {result}")
    for plat, cnt in counts.items():
        print(f"  {plat}: {cnt}건")
    print("=" * 50)


if __name__ == "__main__":
    run()
