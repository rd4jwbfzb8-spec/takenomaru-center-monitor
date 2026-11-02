
import json
import os
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

OFFICIAL_URL = (
    "https://www.nakakumin.com/"
    "takenomaruchikusen/smartphone/"
)

BASE_URL = "https://www.nakakumin.com/takenomaruchikusen/"

KEYWORDS = [
    "予約",
    "空き状況",
    "空き照会",
    "施設予約",
    "reserve1"
]

os.makedirs("artifacts", exist_ok=True)

report = {
    "test_time": datetime.now(timezone.utc).isoformat(),
    "official_url": OFFICIAL_URL,
    "final_url": "",
    "page_title": "",
    "links": [],
    "buttons": [],
    "forms": [],
    "iframes": [],
    "scripts": [],
    "booking_candidates": [],
    "other_page_candidates": {},
    "errors": []
}


def find_candidates(items):
    result = []

    for item in items:
        combined = (
            str(item.get("text", "")) + " " +
            str(item.get("href", "")) + " " +
            str(item.get("onclick", ""))
        ).lower()

        if any(word.lower() in combined for word in KEYWORDS):
            result.append(item)

    return result


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(locale="ja-JP")
    page.set_default_timeout(20000)

    try:
        print("1. 打开竹之丸官网")

        response = page.goto(
            OFFICIAL_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(3000)

        print("官网地址：", page.url)
        print("页面标题：", page.title())
        print("HTTP状态：", response.status if response else None)

        print("2. 读取官网所有链接")

        report["links"] = page.locator("a").evaluate_all("""
            elements => elements.map(a => ({
                text: (a.innerText || a.textContent || "").trim(),
                href: a.href,
                onclick: a.getAttribute("onclick")
            }))
        """)

        report["booking_candidates"] = find_candidates(
            report["links"]
        )

        print("3. 预约相关链接")
        print(json.dumps(
            report["booking_candidates"],
            ensure_ascii=False,
            indent=2
        ))

        print("4. 读取按钮")

        report["buttons"] = page.locator(
            "button, input[type=button], input[type=submit]"
        ).evaluate_all("""
            elements => elements.map(e => ({
                text: (e.innerText || e.value || "").trim(),
                type: e.type,
                name: e.name,
                onclick: e.getAttribute("onclick")
            }))
        """)

        print("5. 读取表单")

        report["forms"] = page.locator("form").evaluate_all("""
            elements => elements.map(f => ({
                action: f.action,
                method: f.method,
                html: f.outerHTML.slice(0, 3000)
            }))
        """)

        print("6. 读取 iframe")

        report["iframes"] = page.locator("iframe").evaluate_all("""
            elements => elements.map(f => ({
                src: f.src,
                title: f.title
            }))
        """)

        print("7. 读取 JavaScript 文件")

        report["scripts"] = page.locator(
            "script[src]"
        ).evaluate_all("""
            elements => elements.map(s => s.src)
        """)

        print("8. 检查官网其他页面")

        for path in [
            "guide.html",
            "sisetu.html",
            "news.cgi"
        ]:
            try:
                test_page = browser.new_page(locale="ja-JP")

                test_page.goto(
                    BASE_URL + path,
                    wait_until="domcontentloaded",
                    timeout=30000
                )

                test_page.wait_for_timeout(1000)

                found = test_page.locator("a").evaluate_all("""
                    elements => elements.map(a => ({
                        text: (a.innerText || a.textContent || "").trim(),
                        href: a.href,
                        onclick: a.getAttribute("onclick")
                    }))
                """)

                matches = find_candidates(found)

                report["other_page_candidates"][path] = matches

                print(
                    path,
                    json.dumps(matches, ensure_ascii=False)
                )

                test_page.close()

            except Exception as e:
                report["errors"].append(
                    path + ": " + repr(e)
                )

        report["final_url"] = page.url
        report["page_title"] = page.title()

        print("入口定位测试完成")

    except Exception as e:
        report["errors"].append(repr(e))
        print("测试错误：", repr(e))

    finally:
        with open(
            "artifacts/report.json",
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                report,
                f,
                ensure_ascii=False,
                indent=2
            )

        print("报告已保存：artifacts/report.json")
        browser.close()
