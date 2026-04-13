"""
이랜서(Elancer) 크롤러 — Playwright 기반 (v4 homepage-discover)
- 홈페이지에서 프로젝트 목록 링크 자동 발견
- 후보 URL + 홈페이지 네비 링크 탐색
"""

import asyncio
import csv
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright

HOMEPAGE = "https://www.elancer.co.kr/"
# 홈페이지 발견 전 우선 시도할 URL (흔한 패턴)
SEED_URLS = [
    "https://www.elancer.co.kr/project/pjtList",
    "https://www.elancer.co.kr/project/list",
    "https://www.elancer.co.kr/project",
    "https://www.elancer.co.kr/project/search",
    "https://www.elancer.co.kr/project/pjt_list",
    "https://www.elancer.co.kr/freelance/project",
    "https://www.elancer.co.kr/outsource/project",
    "https://www.elancer.co.kr/project/projectList",
    "https://www.elancer.co.kr/project/all",
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
CSV_PATH = os.path.join(OUT_DIR, f"elancer_{TODAY}.csv")


async def dump_debug(page, name: str):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    try:
        await page.screenshot(path=os.path.join(DEBUG_DIR, f"elancer_{name}.png"), full_page=True)
        html = await page.content()
        with open(os.path.join(DEBUG_DIR, f"elancer_{name}.html"), "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        print(f"[elancer] 디버그 저장 실패: {e}")


async def extract_cards(page) -> list:
    """프로젝트 상세 링크를 찾아 카드 추출"""
    return await page.evaluate("""() => {
        const candidates = document.querySelectorAll('a');
        const seen = new Set();
        const result = [];
        for (const a of candidates) {
            const href = a.getAttribute('href') || '';
            // 프로젝트 ID 포함 링크 패턴 (다양한 패턴 대응)
            const pidMatch =
                href.match(/[?&](?:pjt_no|project_no|projectId|pjtId|pjt_id|pjtNo|projectNo)=(\\d+)/i) ||
                href.match(/\\/project[_/](?:view|detail|info)?[=/]?(\\d+)/i) ||
                href.match(/\\/pjt[_/](?:view|detail)?[=/]?(\\d+)/i) ||
                href.match(/\\/(?:project|pjt)\\/(\\d{4,})/i);
            if (!pidMatch) continue;
            const pid = pidMatch[1];
            if (seen.has(pid)) continue;
            seen.add(pid);
            let node = a;
            for (let i = 0; i < 6; i++) {
                if (!node.parentElement) break;
                node = node.parentElement;
                if (node.innerText && node.innerText.length > 100) break;
            }
            result.push({
                pid: pid,
                href: href,
                title: (a.innerText || a.getAttribute('title') || '').trim(),
                text: (node.innerText || '').trim().slice(0, 1000),
            });
        }
        return result;
    }""")


async def discover_from_homepage(page) -> list:
    """홈페이지에서 프로젝트 목록 페이지 후보 링크 추출"""
    return await page.evaluate("""() => {
        const links = document.querySelectorAll('a');
        const candidates = [];
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            const text = (a.innerText || '').trim();
            // 상세 페이지 제외 (ID 포함된 URL은 제외)
            if (/\\d{4,}/.test(href)) continue;
            // 후보: href에 project/pjt가 들어가거나, 텍스트에 "프로젝트 찾기/목록/리스트" 포함
            const hrefMatch = /project|pjt|outsource|freelance/i.test(href);
            const textMatch = /프로젝트|외주|찾기|목록/.test(text);
            if (hrefMatch || textMatch) {
                // 절대 URL 변환
                let abs = href;
                if (href.startsWith('/')) abs = location.origin + href;
                else if (!href.startsWith('http')) continue;
                if (!abs.includes('elancer.co.kr')) continue;
                candidates.push({href: abs, text: text.slice(0, 50)});
            }
        }
        // 중복 제거
        const seen = new Set();
        return candidates.filter(c => {
            if (seen.has(c.href)) return false;
            seen.add(c.href);
            return true;
        });
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

    job = ""
    for kw in ["개발", "디자인", "기획", "퍼블리싱", "데이터", "인프라", "QA", "PM"]:
        if kw in text:
            job = kw
            break

    return {
        "title": title[:200],
        "amount": amount,
        "duration": duration,
        "regdate": regdate,
        "location": location,
        "job": job,
    }


async def try_url(page, url: str) -> tuple:
    """URL 시도 → (status, cards 개수) 반환"""
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        await page.wait_for_timeout(2500)
        status = resp.status if resp else 0
        if status >= 400:
            return status, []
        cards = await extract_cards(page)
        return status, cards
    except Exception as e:
        print(f"[elancer]   예외: {e}")
        return 0, []


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

        print("[elancer] 수집 시작")

        # 1. SEED URL 시도
        working_url = None
        for url in SEED_URLS:
            print(f"[elancer] seed 시도: {url}")
            status, cards = await try_url(page, url)
            print(f"[elancer]   status={status}, 카드 {len(cards)}개")
            if status == 200 and cards:
                working_url = url
                break

        # 2. SEED 실패 → 홈페이지에서 링크 발견
        if not working_url:
            print("[elancer] 홈페이지에서 링크 탐색")
            try:
                await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                await page.wait_for_timeout(3000)
                await dump_debug(page, "homepage")
                discovered = await discover_from_homepage(page)
                print(f"[elancer] 홈페이지 후보 링크 {len(discovered)}개")
                for d in discovered[:15]:
                    print(f"  → {d['text']} | {d['href']}")
                    status, cards = await try_url(page, d['href'])
                    print(f"    status={status}, 카드 {len(cards)}개")
                    if status == 200 and cards:
                        working_url = d['href']
                        break
            except Exception as e:
                print(f"[elancer] 홈페이지 탐색 실패: {e}")

        if not working_url:
            print("[elancer] 접근 가능한 URL 없음")
            await dump_debug(page, "no_url")
            await browser.close()
            with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(HEADERS_ROW)
            return

        print(f"[elancer] 사용 URL: {working_url}")
        await dump_debug(page, "page1")

        # 3. 페이지네이션
        for pg in range(1, MAX_PAGES + 1):
            if pg > 1:
                sep = "&" if "?" in working_url else "?"
                url = f"{working_url}{sep}page={pg}"
                print(f"[elancer] page {pg}: {url}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                    await page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"[elancer] page {pg} 실패: {e}")
                    break

            cards = await extract_cards(page)
            print(f"[elancer] page {pg} 카드 {len(cards)}개")

            if not cards:
                await dump_debug(page, f"empty_p{pg}")
                break

            new_count = 0
            for c in cards:
                pid = c["pid"]
                if pid in seen:
                    continue
                seen.add(pid)
                parsed = parse_card(c)
                if not parsed["title"]:
                    continue
                rows.append([
                    "", "이랜서", parsed["title"], parsed["regdate"], parsed["amount"],
                    parsed["duration"], "", parsed["job"], "", parsed["location"], now_str,
                ])
                new_count += 1

            if new_count == 0:
                print(f"[elancer] page {pg} 새 항목 없음 — 종료")
                break

        await browser.close()

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADERS_ROW)
        for i, r in enumerate(rows):
            r[0] = str(i + 1)
            w.writerow(r)

    print(f"[elancer] 완료: {len(rows)}건 → {CSV_PATH}")


if __name__ == "__main__":
    asyncio.run(crawl())
