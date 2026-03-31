"""
위시켓(Wishket) 크롤러 — Playwright 기반
Apps Script의 UrlFetchApp로는 JS 렌더링 불가 → Playwright headless로 전환
"""

import csv
import os
import re
import sys
import asyncio
from datetime import datetime

from playwright.async_api import async_playwright, Page, Browser

# ── 설정 ────────────────────────────────────────────────
BASE_URL = "https://www.wishket.com/project/"
MAX_PAGES = 30
PAGE_TIMEOUT = 60_000

HEADERS_ROW = [
    "No.", "플랫폼", "프로젝트 제목", "등록일", "금액",
    "예상기간", "기간제/외주", "직무", "스킬", "근무지", "수집일시",
]

OUT_DIR = os.environ.get("OUT_DIR", "out")
DEBUG_DIR = os.path.join(OUT_DIR, "debug")
TODAY = datetime.now().strftime("%y%m%d")
CSV_PATH = os.path.join(OUT_DIR, f"wishket_{TODAY}.csv")


async def setup_browser() -> tuple:
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    page = await context.new_page()
    return pw, browser, context, page


async def parse_project_card(card, idx: int, collect_time: str) -> list | None:
    try:
        title_el = await card.query_selector("h4, .project-title, a.title")
        title = (await title_el.inner_text()).strip() if title_el else ""
        if not title:
            return None

        date_el = await card.query_selector(".regist-day, .project-date, time, .date")
        reg_date = ""
        if date_el:
            reg_date = (await date_el.inner_text()).strip()
            reg_date = re.sub(r"등록일?\\s*:?\\s*", "", reg_date).strip()

        price_el = await card.query_selector(".project-price, .price, .amount")
        amount = (await price_el.inner_text()).strip() if price_el else "협의"

        period_el = await card.query_selector(".project-period, .period, .duration")
        duration = (await period_el.inner_text()).strip() if period_el else ""
        duration = re.sub(r"예상\\s*기간\\s*:?\\s*", "", duration).strip()

        type_el = await card.query_selector(".project-type, .type-badge, .work-type")
        pjt_type = (await type_el.inner_text()).strip() if type_el else ""

        role_el = await card.query_selector(".project-category, .category, .job-role")
        job_role = (await role_el.inner_text()).strip() if role_el else ""

        skill_els = await card.query_selector_all(".project-skill .tag, .skill-tag, .tech-stack span, .tag")
        skills = []
        for s in skill_els:
            txt = (await s.inner_text()).strip()
            if txt and txt not in skills:
                skills.append(txt)
        skills_str = ", ".join(skills)

        loc_el = await card.query_selector(".project-location, .location, .place")
        location = (await loc_el.inner_text()).strip() if loc_el else ""

        return [idx, "위시켓", title, reg_date, amount, duration, pjt_type, job_role, skills_str, location, collect_time]
    except Exception as e:
        print(f"[wishket] 카드 파싱 오류: {e}")
        return None


async def crawl_page(page: Page, url: str) -> list:
    await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    await page.wait_for_timeout(2000)
    cards = await page.query_selector_all(
        ".project-list .project-card, .project-item, [class*='ProjectCard'], .list-container > li, .project-list > div"
    )
    return cards


async def crawl() -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DEBUG_DIR, exist_ok=True)
    collect_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    all_rows: list[list] = []

    pw, browser, context, page = await setup_browser()

    try:
        print("[wishket] 수집 시작")
        for pg in range(1, MAX_PAGES + 1):
            url = f"{BASE_URL}?page={pg}"
            print(f"[wishket] page {pg}: {url}")
            try:
                cards = await crawl_page(page, url)
            except Exception as e:
                print(f"[wishket] page {pg} 로드 실패: {e}")
                try:
                    await page.screenshot(path=os.path.join(DEBUG_DIR, f"wishket_error_p{pg}.png"))
                except:
                    pass
                break

            if not cards:
                print(f"[wishket] page {pg}: 카드 없음 → 종료")
                try:
                    await page.screenshot(path=os.path.join(DEBUG_DIR, f"wishket_empty_p{pg}.png"))
                except:
                    pass
                break

            page_rows = 0
            for card in cards:
                row = await parse_project_card(card, len(all_rows) + 1, collect_time)
                if row:
                    all_rows.append(row)
                    page_rows += 1

            print(f"[wishket] page {pg}: {page_rows}건  누적 {len(all_rows)}건")

            next_btn = await page.query_selector("a.next, .pagination .next, [aria-label='Next']")
            if not next_btn:
                print("[wishket] 마지막 페이지 도달")
                break
    finally:
        await browser.close()
        await pw.stop()

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS_ROW)
        writer.writerows(all_rows)

    print(f"[wishket] 완료: {len(all_rows)}건 → {CSV_PATH}")
    return CSV_PATH


def main():
    return asyncio.run(crawl())


if __name__ == "__main__":
    path = main()
    print(f"CSV saved: {path}")
