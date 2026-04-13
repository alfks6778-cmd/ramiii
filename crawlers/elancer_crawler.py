"""
이랜서(Elancer) 크롤러 — Playwright 기반 (v5 XHR intercept)
- /list-partner 페이지 + 홈페이지 접근
- XHR 응답 가로채기로 API 데이터 자동 캡처
- DOM broad 추출 fallback
- 모든 링크 패턴 디버그 덤프
"""

import asyncio
import csv
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright

URLS_TO_TRY = [
    "https://www.elancer.co.kr/list-partner",
    "https://www.elancer.co.kr/list-partner?pf=턴키",
    "https://www.elancer.co.kr/list-partner?pf=상주",
    "https://www.elancer.co.kr/",
]
PAGE_TIMEOUT = 60000
MAX_PAGES = 5
HEADERS_ROW = [
    "No.", "플랫폼", "프로젝트 제목", "등록일", "금액",
    "예상기간", "기간제/외주", "직무", "스킬", "근무지", "수집일시",
]

OUT_DIR = os.environ.get("OUT_DIR", "out")
DEBUG_DIR = os.path.join(OUT_DIR, "debug")
TODAY = datetime.now().strftime("%y%m%d")
CSV_PATH = os.path.join(OUT_DIR, f"elancer_{TODAY}.csv")

# XHR로 캡처한 프로젝트 데이터
captured_api_items = []


async def dump_debug(page, name: str):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    try:
        await page.screenshot(path=os.path.join(DEBUG_DIR, f"elancer_{name}.png"), full_page=True)
        html = await page.content()
        with open(os.path.join(DEBUG_DIR, f"elancer_{name}.html"), "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        print(f"[elancer] 디버그 저장 실패: {e}")


async def dump_all_links(page, name: str):
    """디버그: 페이지의 모든 링크 패턴 저장"""
    links = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a')).map(a => ({
            href: a.getAttribute('href') || '',
            text: (a.innerText || '').trim().slice(0, 80),
        })).filter(x => x.href && x.href !== '#');
    }""")
    os.makedirs(DEBUG_DIR, exist_ok=True)
    with open(os.path.join(DEBUG_DIR, f"elancer_{name}_links.json"), "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=2)
    print(f"[elancer] 페이지 링크 {len(links)}개 저장됨")
    for link in links[:20]:
        print(f"  → {link['href'][:80]} | {link['text'][:40]}")
    return links


def on_response(response):
    """XHR/Fetch 응답 가로채기 — JSON에서 프로젝트 데이터 추출"""
    url = response.url
    ct = response.headers.get("content-type", "")
    if "json" not in ct and "javascript" not in ct:
        return
    try:
        # 비동기 함수 안에서 호출되므로 동기로 처리 불가
        # 대신 URL만 기록하고 나중에 처리
        pass
    except Exception:
        pass


async def capture_xhr(page, url: str):
    """페이지 로드하면서 모든 JSON 응답 캡처"""
    captured = []

    async def handle_response(response):
        ct = response.headers.get("content-type", "")
        resp_url = response.url
        if "json" in ct or "javascript" in ct:
            try:
                body = await response.text()
                if len(body) > 100:  # 의미 있는 크기만
                    try:
                        data = json.loads(body)
                        captured.append({"url": resp_url, "data": data})
                    except Exception:
                        pass
            except Exception:
                pass

    page.on("response", handle_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # AJAX 로딩 대기
        await page.wait_for_timeout(5000)
    except Exception as e:
        print(f"[elancer] 페이지 로드 실패: {e}")

    page.remove_listener("response", handle_response)
    return captured


def extract_projects_from_json(captured: list) -> list:
    """캡처된 JSON 응답에서 프로젝트 데이터 추출"""
    results = []

    def walk(node, depth=0):
        if depth > 10:
            return
        if isinstance(node, list):
            # 리스트 내 항목이 프로젝트처럼 보이는지 확인
            project_like = []
            for item in node:
                if isinstance(item, dict):
                    keys_str = " ".join(str(k).lower() for k in item.keys())
                    # 프로젝트 관련 키가 있으면 후보
                    if any(kw in keys_str for kw in [
                        "pjt", "project", "title", "subject", "name",
                        "budget", "period", "skill", "duty", "area",
                    ]):
                        project_like.append(item)
            if len(project_like) >= 3:
                results.extend(project_like)
            for item in node:
                walk(item, depth + 1)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v, depth + 1)

    for cap in captured:
        walk(cap["data"])

    # 중복 제거 (title 기준)
    seen = set()
    deduped = []
    for item in results:
        title = ""
        for k in ["pjtTitle", "title", "subject", "projectTitle", "pjt_title", "name"]:
            if k in item and item[k]:
                title = str(item[k]).strip()
                break
        if title and title not in seen:
            seen.add(title)
            deduped.append(item)

    return deduped


def _get(item: dict, *keys, default=""):
    for k in keys:
        v = item.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default


def json_item_to_row(item: dict, now_str: str) -> list:
    title = _get(item, "pjtTitle", "title", "subject", "projectTitle", "pjt_title", "name")
    regdate = _get(item, "regDate", "reg_date", "createdAt", "created_at", "createDate")[:10]

    amount = _get(item, "budget", "pjtBudget", "amount", "price")
    if not amount:
        bmin = item.get("budgetMin") or item.get("minBudget")
        bmax = item.get("budgetMax") or item.get("maxBudget")
        if bmin and bmax:
            amount = f"{bmin}~{bmax}"

    duration = _get(item, "period", "pjtPeriod", "duration", "expectedPeriod", "workPeriod")
    work_type = _get(item, "pjtType", "workType", "type", "pf")
    job = _get(item, "duty", "dutyName", "job", "category", "field")
    skill = _get(item, "skill", "skills", "techStack", "pjtSkill")
    location = _get(item, "area", "location", "region", "pjtArea", "workArea")

    return [
        "", "이랜서", title[:200], regdate, amount,
        duration, work_type, job, skill[:100], location, now_str,
    ]


async def extract_dom_broad(page) -> list:
    """폭넓은 DOM 추출 — 반복되는 카드형 요소 탐색"""
    return await page.evaluate("""() => {
        // 모든 a 태그의 href 중 숫자 ID를 포함한 것
        const links = document.querySelectorAll('a');
        const seen = new Set();
        const result = [];
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            // 다양한 ID 패턴
            const m = href.match(/(\\d{3,})/);
            if (!m) continue;
            const pid = m[1];
            if (seen.has(pid)) continue;
            seen.add(pid);
            // 부모 컨테이너 탐색
            let node = a;
            for (let i = 0; i < 8; i++) {
                if (!node.parentElement) break;
                node = node.parentElement;
                if (node.innerText && node.innerText.length > 60) break;
            }
            const text = (node.innerText || '').trim();
            if (text.length < 20) continue;
            result.push({
                pid: pid,
                href: href,
                title: (a.innerText || a.getAttribute('title') || '').trim(),
                text: text.slice(0, 1000),
            });
        }

        // Fallback: 반복 패턴 찾기 — 같은 class의 div/li 중 텍스트 50자 이상
        if (result.length === 0) {
            const allElems = document.querySelectorAll('div, li, article, section');
            const classGroups = {};
            for (const el of allElems) {
                const cls = el.className || '';
                if (!cls || el.innerText.trim().length < 30) continue;
                if (!classGroups[cls]) classGroups[cls] = [];
                classGroups[cls].push(el);
            }
            // 3개 이상 반복되는 그룹 = 카드 패턴
            for (const [cls, els] of Object.entries(classGroups)) {
                if (els.length >= 3 && els.length <= 100) {
                    for (const el of els) {
                        const text = el.innerText.trim();
                        if (text.length > 30) {
                            result.push({
                                pid: 'dom_' + result.length,
                                href: '',
                                title: text.split('\\n')[0].slice(0, 100),
                                text: text.slice(0, 1000),
                            });
                        }
                    }
                    if (result.length > 0) break;
                }
            }
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

    job = ""
    for kw in ["개발", "SI", "디자인", "기획", "퍼블", "데이터", "인프라", "QA", "PM"]:
        if kw in text:
            job = kw
            break

    return [
        "", "이랜서", title[:200], regdate, amount,
        duration, "", job, "", location, now_str,
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

        print("[elancer] 수집 시작")

        for url in URLS_TO_TRY:
            print(f"[elancer] XHR 캡처 시도: {url}")

            # XHR 응답 캡처하면서 페이지 로드
            captured = await capture_xhr(page, url)
            print(f"[elancer]   JSON 응답 {len(captured)}개 캡처")

            # 캡처된 JSON 저장 (디버그)
            if captured:
                os.makedirs(DEBUG_DIR, exist_ok=True)
                safe_name = url.split("/")[-1][:30].replace("?", "_")
                with open(os.path.join(DEBUG_DIR, f"elancer_xhr_{safe_name}.json"), "w", encoding="utf-8") as f:
                    summary = [{"url": c["url"][:200], "keys": list(c["data"].keys()) if isinstance(c["data"], dict) else f"array[{len(c['data'])}]" if isinstance(c["data"], list) else type(c["data"]).__name__} for c in captured]
                    json.dump(summary, f, ensure_ascii=False, indent=2)

            # JSON에서 프로젝트 추출
            projects = extract_projects_from_json(captured)
            print(f"[elancer]   JSON 프로젝트 {len(projects)}개")

            if projects:
                for item in projects:
                    title = _get(item, "pjtTitle", "title", "subject", "projectTitle", "name")
                    if not title or title in seen:
                        continue
                    seen.add(title)
                    rows.append(json_item_to_row(item, now_str))
                print(f"[elancer]   JSON에서 {len(rows)}건 추출")

            # DOM도 시도
            await dump_debug(page, url.split("/")[-1][:20].replace("?", "_"))
            await dump_all_links(page, url.split("/")[-1][:20].replace("?", "_"))

            dom_cards = await extract_dom_broad(page)
            print(f"[elancer]   DOM 카드 {len(dom_cards)}개")

            for c in dom_cards:
                pid = c["pid"]
                title_key = c.get("title", "")[:50]
                key = pid if pid and not pid.startswith("dom_") else title_key
                if not key or key in seen:
                    continue
                seen.add(key)
                row = parse_dom_card(c, now_str)
                if row[2] and len(row[2]) > 5:
                    rows.append(row)

            if rows:
                print(f"[elancer] {url}에서 총 {len(rows)}건 — 수집 완료")
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
