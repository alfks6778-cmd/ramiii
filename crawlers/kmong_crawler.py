"""
크몽(Kmong) 엔터프라이즈 크롤러 — Playwright 기반 (v6 NEXT_DATA 활용)
- URL: kmong.com/custom-project/requests
- __NEXT_DATA__ 우선, DOM fallback
- __NEXT_DATA__ 항목을 row로 변환
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


def _first_str(item: dict, *keys, default="") -> str:
    for k in keys:
        v = item.get(k)
        if v and isinstance(v, (str, int, float)):
            s = str(v).strip()
            if s:
                return s
    return default


async def try_next_data(page) -> list:
    """__NEXT_DATA__에서 프로젝트 리스트 추출"""
    try:
        data = await page.evaluate("""() => {
            const el = document.getElementById('__NEXT_DATA__');
            return el ? el.textContent : null;
        }""")
        if not data:
            return []
        obj = json.loads(data)
        # dehydrated 쿼리 캐시 저장 디버그
        try:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            with open(os.path.join(DEBUG_DIR, "kmong_next_data.json"), "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        results = []
        def walk(node):
            if isinstance(node, list):
                for item in node:
                    if isinstance(item, dict):
                        # 프로젝트 후보 판단: title/projectTitle 있고 길이 > 5
                        title = _first_str(item, "title", "projectTitle", "project_title", "name", "subject")
                        if title and len(title) > 5:
                            # id/pid도 있어야 진짜 프로젝트
                            pid = _first_str(item, "id", "projectId", "project_id", "pjtNo", "uid", "no")
                            if pid:
                                results.append(item)
                        walk(item)
                    else:
                        walk(item)
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v)
        walk(obj)
        # 중복 제거
        dedup = {}
        for item in results:
            pid = _first_str(item, "id", "projectId", "project_id", "pjtNo", "uid", "no")
            if pid and pid not in dedup:
                dedup[pid] = item
        return list(dedup.values())
    except Exception as e:
        print(f"[kmong] __NEXT_DATA__ 파싱 실패: {e}")
        return []


def next_item_to_row(item: dict, now_str: str) -> list:
    """__NEXT_DATA__ 항목을 row로 변환"""
    title = _first_str(item, "title", "projectTitle", "project_title", "name", "subject")
    regdate = _first_str(item, "createdAt", "created_at", "regDate", "reg_date", "createDate")[:10]

    budget_min = item.get("budgetMin") or item.get("budget_min") or item.get("minBudget")
    budget_max = item.get("budgetMax") or item.get("budget_max") or item.get("maxBudget")
    budget = _first_str(item, "budget", "amount", "price")
    if budget_min and budget_max:
        amount = f"{budget_min:,}~{budget_max:,}원" if isinstance(budget_min, (int, float)) else f"{budget_min}~{budget_max}"
    elif budget:
        amount = budget
    elif budget_min:
        amount = f"{budget_min:,}원" if isinstance(budget_min, (int, float)) else str(budget_min)
    else:
        amount = ""

    duration = _first_str(item, "duration", "period", "expectedDuration", "workPeriod")
    category = _first_str(item, "category", "categoryName", "job", "jobCategory", "field")
    location = _first_str(item, "location", "area", "region", "workLocation")
    work_type = _first_str(item, "workType", "work_type", "type", "contractType")

    return [
        "", "크몽", title[:200], regdate, amount,
        str(duration)[:50], str(work_type)[:30], str(category)[:50], "",
        str(location)[:50], now_str,
    ]


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


def parse_dom_card(card: dict, now_str: str) -> list:
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

    return [
        "", "크몽", title[:200], regdate, amount,
        duration, "", "", "", location, now_str,
    ]


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

            # __NEXT_DATA__ 우선
            next_items = await try_next_data(page)
            print(f"[kmong] page {pg} __NEXT_DATA__에서 {len(next_items)}개")

            dom_cards = await extract_dom(page)
            print(f"[kmong] page {pg} DOM에서 {len(dom_cards)}개")

            if not next_items and not dom_cards:
                await dump_debug(page, f"empty_p{pg}")
                break

            new_count = 0

            # NEXT_DATA 항목 먼저 처리
            for item in next_items:
                pid = _first_str(item, "id", "projectId", "project_id", "pjtNo", "uid", "no")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                row = next_item_to_row(item, now_str)
                if row[2]:  # title 있으면
                    rows.append(row)
                    new_count += 1

            # DOM fallback
            for c in dom_cards:
                pid = c["pid"]
                if pid in seen:
                    continue
                seen.add(pid)
                row = parse_dom_card(c, now_str)
                if row[2]:
                    rows.append(row)
                    new_count += 1

            print(f"[kmong] page {pg} 신규 {new_count}개")

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
