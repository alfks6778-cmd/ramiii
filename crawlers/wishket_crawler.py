"""
위시켓(Wishket) 크롤러 — Playwright 기반 (v5 compact)
- domcontentloaded + 명시적 selector 대기
- 카드 셀렉터 자동탐색 + fallback
- 디버그 스크린샷/HTML 저장
"""

import asyncio
import csv
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright

BASE_URL = "https://www.wishket.com/project/"
PAGE_TIMEOUT = 60000
MAX_PAGES = 10
HEADERS_ROW = [
    "No.", "플랫폼", "프로젝트 제목", "등록일", "금액",
    "예상기간", "기간제/외주", "직무", "스킬", "근무지", "수집일시",
]

OUT_DIR = os.environ.get("OUT_DIR", "out")
DEBUG_DIR = os.path.join(OUT_DIR, "debug")
TODAY = datetime.now().strftime("%y%m%d")
CSV_PATH = os.path.join(OUT_DIR, f"wishket_{TODAY}.csv")

# 위시켓은 SPA — /project/NNN/ 형태의 앵커를 찾아 카드 컨테이너 추적
CARD_SELECTORS = [
    'div.project-info-box',
    'a[href^="/project/"][href$="/"]',
    'div[class*="project"][class*="card"]',
    'div[class*="ProjectCard"]',
    'li[class*="project"]',
]


async def dump_debug(page, name: str):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    try:
        await page.screenshot(path=os.path.join(DEBUG_DIR, f"wishket_{name}.png"), full_page=True)
        html = await page.content()
        with open(os.path.join(DEBUG_DIR, f"wishket_{name}.html"), "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        print(f"[wishket] 디버그 저장 실패: {e}")


async def extract_cards(page):
    """페이지에서 프로젝트 카드 추출 — 다중 전략"""
    cards = await page.evaluate("""() => {
        const links = document.querySelectorAll('a[href^="/project/"]');
        const seen = new Set();
        const result = [];
        for (const a of links) {
            const m = a.getAttribute('href').match(/^\\/project\\/(\\d+)\\/?$/);
            if (!m) continue;
            const pid = m[1];
            if (seen.has(pid)) continue;
            seen.add(pid);
            let node = a;
            for (let i = 0; i < 5; i++) {
                if (!node.parentElement) break;
                node = node.parentElement;
                if (node.innerText && node.innerText.length > 80) break;
            }
            result.push({
                pid: pid,
                href: a.getAttribute('href'),
                title: (a.innerText || '').trim() || (a.getAttribute('title') || ''),
                text: (node.innerText || '').trim().slice(0, 800),
            });
        }
        return result;
    }""")
    return cards


def parse_card_text(card: dict) -> dict:
    """카드의 text 블록에서 필드 추출"""
    text = card.get("text", "")
    title = card.get("title") or ""

    if not title:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        title = lines[0] if lines else ""

    amount = ""
    m = re.search(r"(\d[\d,]*\s*만?\s*원(?:\s*~\s*\d[\d,]*\s*만?\s*원)?)", text)
    if m:
        amount = m.group(1).strip()

    duration = ""
    m = re.search(r"(\d+\s*(?:일|주|개월|달)(?:\s*~\s*\d+\s*(?:일|주|개월|달))?)", text)
    if m:
        duration = m.group(1).strip()

    regdate = ""
    m = re.search(r"(\d{4}[./-]\d{1,2}[./-]\d{1,2})", text)
    if m:
        regdate = m.group(1)

    location = ""
    m = re.search(r"(서울|경기|인천|부산|대구|대전|광주|울산|세종|강원|충[북남]|전[북남]|경[북남]|제주|재택|원격)(?:[^\n]*)", text)
    if m:
        location = m.group(0)[:30].strip()

    return {
        "title": title[:200],
        "amount": amount,
        "duration": duration,
        "regdate": regdate,
        "location": location,
        "raw": text[:200],
    }


async def crawl():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DEBUG_DIR, exist_ok=True)

    rows = []
    seen_ids = set()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="ko-KR",
        )
        page = await context.new_page()

        print("[wishket] 수집 시작")
        for pg in range(1, MAX_PAGES + 1):
            url = f"{BASE_URL}?page={pg}"
            print(f"[wishket] page {pg}: {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                try:
                    await page.wait_for_selector('a[href^="/project/"]', timeout=15000)
                except Exception:
                    pass
                await page.wait_for_timeout(3000)
            except Exception as e:
                print(f"[wishket] page {pg} 로드 실패: {e}")
                await dump_debug(page, f"error_p{pg}")
                break

            if pg == 1:
                await dump_debug(page, "page1")

            cards = await extract_cards(page)
            print(f"[wishket] page {pg} 카드 {len(cards)}개 추출")

            if not cards:
                await dump_debug(page, f"empty_p{pg}")
                break

            new_count = 0
            for c in cards:
                pid = c["pid"]
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                parsed = parse_card_text(c)
                if not parsed["title"]:
                    continue
                rows.append([
                    "", "위시켓", parsed["title"], parsed["regdate"], parsed["amount"],
                    parsed["duration"], "", "", "", parsed["location"], now_str,
                ])
                new_count += 1

            if new_count == 0:
                print(f"[wishket] page {pg} 새 항목 없음 — 종료")
                break

        await browser.close()

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADERS_ROW)
        for i, r in enumerate(rows):
            r[0] = str(i + 1)
            w.writerow(r)

    print(f"[wishket] 완료: {len(rows)}건 → {CSV_PATH}")


if __name__ == "__main__":
    asyncio.run(crawl())
