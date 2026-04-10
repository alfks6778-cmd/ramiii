"""
크몽(Kmong) 엔터프라이즈 크롤러 — Playwright 기반 (v5 compact)
- URL: kmong.com/custom-project/requests
- domcontentloaded + 명시적 selector 대기
- __NEXT_DATA__ 파싱 + DOM fallback
"""

import asyncio
import csv
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright

CANDIDATE_URLS = [
    "https://kmong.com/custom-project/requests",
    "https://kmong.com/enterprise/requests",
    "https://kmong.com/enterprise-v2/requests",
]
PAGE_TIMEOUT = 60000
MAX_PAGES = 10
HEADERS_ROW = [
    "No.", "플랫폼", "프로젝트 제목", "등록일", "금액",
    "예상기간", "기간제/외주", "직무", "스킬", "근무지", "수집일시",
]

OUT_DIR = os.environ.get("OUT_DIR", "out")
DEBUG_DIR = os.path.join(OUT_DIR, "debug")
TODAY = datetime.now().strftime("%y%m%d")
CSV_PATH = os.path.join(OUT_DIR, f"kmong_{TODAY}.csv")


async def dump_debug(page, name: str):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    try:
        await page.screenshot(path=os.path.join(DEBUG_DIR, f"kmong_{name}.png"), full_page=True)
        html = await page.content()
        with open(os.path.join(DEBUG_DIR, f"kmong_{name}.html"), "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        print(f"[kmong] 디버그 저장 실패: {e}")


async def try_next_data(page) -> list:
    """__NEXT_DATA__에서 프로젝트 리스트 추출 시도"""
    try:
        data = await page.evaluate("""() => {
            const el = document.getElementById('__NEXT_DATA__');
            return el ? el.textContent : null;
        }""")
        if not data:
            return []
        obj = json.loads(data)
        results = []
        def walk(node):
            if isinstance(node, list):
                for item in node:
                    if isinstance(item, dict) and any(k in item for k in ("title", "projectTitle", "name")):
                        results.append(item)
                    else:
                        walk(item)
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v)
        walk(obj)
        return results
    except Exception as e:
        print(f"[kmong] __NEXT_DATA__ 파싱 실패: {e}")
        return []


async def extract_dom(page) -> list:
    """DOM에서 카드 추출"""
    return await page.evaluate("""() => {
        const links = document.querySelectorAll('a[href*="/custom-project/"], a[href*="/requests/"], a[href*="/enterprise/"]');
        const seen = new Set();
        const result = [];
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            const m = href.match(/(\\d{3,})/);
            if (!m) continue;
            const pid = m[1];
            if (seen.has(pid)) continue;
            seen.add(pid);
            let node = a;
            for (let i = 0; i < 6; i++) {
                if (!node.parentElement) break;
                node = node.parentElement;
                if (node.innerText && node.innerText.length > 80) break;
            }
            result.push({
                pid: pid,
                href: href,
                title: (a.innerText || a.getAttribute('title') || '').trim(),
                text: (node.innerText || '').trim().slice(0, 800),
            });
        }
        return result;
    }""")


def parse_card(card: dict) -> dict:
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
    m = re.search(r"(서울|경기|인천|부산|대구|대전|광주|울산|세종|강원|충[북남]|전[북남]|경[북남]|제주|재택|원격)", text)
    if m:
        location = m.group(0)

    return {
        "title": title[:200],
        "amount": amount,
        "duration": duration,
        "regdate": regdate,
        "location": location,
    }


async def crawl():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DEBUG_DIR, exist_ok=True)

    rows = []
    seen = set()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="ko-KR",
        )
        page = await context.new_page()

        print("[kmong] 수집 시작")
        working_url = None
        for url in CANDIDATE_URLS:
            try:
                print(f"[kmong] 시도: {url}")
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                await page.wait_for_timeout(3000)
                status = resp.status if resp else 0
                print(f"[kmong]   status={status}")
                if status and status < 400:
                    working_url = url
                    break
            except Exception as e:
                print(f"[kmong]   실패: {e}")

        if not working_url:
            print("[kmong] 접근 가능한 URL 없음")
            await dump_debug(page, "no_url")
            await browser.close()
            with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(HEADERS_ROW)
            return

        await dump_debug(page, "page1")

        for pg in range(1, MAX_PAGES + 1):
            if pg > 1:
                sep = "&" if "?" in working_url else "?"
                url = f"{working_url}{sep}page={pg}"
                print(f"[kmong] page {pg}: {url}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                    await page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"[kmong] page {pg} 실패: {e}")
                    break

            next_items = await try_next_data(page)
            if next_items:
                print(f"[kmong] __NEXT_DATA__에서 {len(next_items)}개")

            dom_cards = await extract_dom(page)
            print(f"[kmong] page {pg} DOM에서 {len(dom_cards)}개")

            if not dom_cards and not next_items:
                await dump_debug(page, f"empty_p{pg}")
                break

            new_count = 0
            for c in dom_cards:
                pid = c["pid"]
                if pid in seen:
                    continue
                seen.add(pid)
                parsed = parse_card(c)
                if not parsed["title"]:
                    continue
                rows.append([
                    "", "크몽", parsed["title"], parsed["regdate"], parsed["amount"],
                    parsed["duration"], "", "", "", parsed["location"], now_str,
                ])
                new_count += 1

            if new_count == 0:
                print(f"[kmong] page {pg} 새 항목 없음 — 종료")
                break

        await browser.close()

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADERS_ROW)
        for i, r in enumerate(rows):
            r[0] = str(i + 1)
            w.writerow(r)

    print(f"[kmong] 완료: {len(rows)}건 → {CSV_PATH}")


if __name__ == "__main__":
    asyncio.run(crawl())
