"""
크몽 엔터프라이즈(Kmong Enterprise) 크롤러 — Playwright 기반
HTTP 403 차단 → 브라우저 자동화로 우회
"""

import csv
import os
import re
import sys
import asyncio
from datetime import datetime

from playwright.async_api import async_playwright, Page

# ── 설정 ────────────────────────────────────────────────
BASE_URL = "https://enterprise.kmong.com/projects"
MAX_PAGES = 30
PAGE_TIMEOUT = 60_000

HEADERS_ROW = [
    "No.", "플랫폼", "프로젝트 제목", "등록일", "금액",
    "예상기간", "기간제/외주", "직무", "스킬", "근무지", "수집일시",
]

OUT_DIR = os.environ.get("OUT_DIR", "out")
DEBUG_DIR = os.path.join(OUT_DIR, "debug")
TODAY = datetime.now().strftime("%y%m%d")
CSV_PATH = os.path.join(OUT_DIR, f"kmong_{TODAY}.csv")


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
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
    """)
    return pw, browser, context, page


async def human_scroll(page: Page):
    for _ in range(3):
        await page.mouse.wheel(0, 400)
        await page.wait_for_timeout(800)


async def parse_project_card(card, idx: int, collect_time: str) -> list | None:
    try:
        title_el = await card.query_selector("h3, h4, .project-title, a[class*='title'], [class*='Title']")
        title = (await title_el.inner_text()).strip() if title_el else ""
        if not title:
            return None

        date_el = await card.query_selector("time, .date, [class*='date'], [class*='Date']")
        reg_date = (await date_el.inner_text()).strip() if date_el else ""

        price_el = await card.query_selector(".price, [class*='price'], [class*='Price'], [class*='budget']")
        amount = (await price_el.inner_text()).strip() if price_el else "협의"

        dur_el = await card.query_selector("[class*='period'], [class*='Period'], [class*='duration']")
        duration = (await dur_el.inner_text()).strip() if dur_el else ""

        type_el = await card.query_selector("[class*='type'], [class*='Type'], .badge")
        pjt_type = (await type_el.inner_text()).strip() if type_el else ""

        cat_el = await card.query_selector("[class*='category'], [class*='Category'], [class*='field']")
        job_role = (await cat_el.inner_text()).strip() if cat_el else ""

        skill_els = await card.query_selector_all(".tag, [class*='tag'], [class*='Tag'], [class*='skill']")
        skills = []
        for s in skill_els:
            txt = (await s.inner_text()).strip()
            if txt and len(txt) < 30 and txt not in skills:
                skills.append(txt)
        skills_str = ", ".join(skills)

        loc_el = await card.query_selector("[class*='location'], [class*='Location'], [class*='place']")
        location = (await loc_el.inner_text()).strip() if loc_el else ""

        return [idx, "크몽", title, reg_date, amount, duration, pjt_type, job_role, skills_str, location, collect_time]
    except Exception as e:
        print(f"[kmong] 카드 파싱 오류: {e}")
        return None


async def crawl() -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DEBUG_DIR, exist_ok=True)
    collect_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    all_rows: list[list] = []

    pw, browser, context, page = await setup_browser()

    try:
        print("[kmong] 수집 시작")
        for pg in range(1, MAX_PAGES + 1):
            url = f"{BASE_URL}?page={pg}"
            print(f"[kmong] page {pg}: {url}")
            try:
                await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
                await page.wait_for_timeout(3000)
                await human_scroll(page)
            except Exception as e:
                print(f"[kmong] page {pg} 로드 실패: {e}")
                try:
                    await page.screenshot(path=os.path.join(DEBUG_DIR, f"kmong_error_p{pg}.png"))
                except:
                    pass
                break

            cards = await page.query_selector_all(
                ".project-card, [class*='ProjectCard'], [class*='project-item'], .list-item, article"
            )

            if not cards:
                print(f"[kmong] page {pg}: 카드 없음 → 종료")
                try:
                    await page.screenshot(path=os.path.join(DEBUG_DIR, f"kmong_empty_p{pg}.png"))
                except:
                    pass
                break

            page_rows = 0
            for card in cards:
                row = await parse_project_card(card, len(all_rows) + 1, collect_time)
                if row:
                    all_rows.append(row)
                    page_rows += 1

            print(f"[kmong] page {pg}: {page_rows}건  누적 {len(all_rows)}건")

            next_btn = await page.query_selector(
                "a.next, .pagination .next:not(.disabled), [aria-label='Next']:not([disabled])"
            )
            if not next_btn:
                print("[kmong] 마지막 페이지 도달")
                break
    finally:
        await browser.close()
        await pw.stop()

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS_ROW)
        writer.writerows(all_rows)

    print(f"[kmong] 완료: {len(all_rows)}건 → {CSV_PATH}")
    return CSV_PATH


def main():
    return asyncio.run(crawl())


if __name__ == "__main__":
    path = main()
    print(f"CSV saved: {path}")
