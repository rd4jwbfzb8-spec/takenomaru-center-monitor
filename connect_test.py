
import json
import os
import sys
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

OFFICIAL_URL = (
    "https://www.nakakumin.com/"
    "takenomaruchikusen/smartphone/"
)

RESERVATION_URL = (
    "https://www.reserve1.jp/studio/member/VisitorLogin.php"
)

os.makedirs("artifacts", exist_ok=True)

report = {
    "test_time": datetime.now(timezone.utc).isoformat(),
    "official_url": OFFICIAL_URL,
    "reservation_url": RESERVATION_URL,
    "final_url": "",
    "page_title": "",
    "grand_options": [],
    "selects": [],
    "forms": [],
    "page_text": "",
    "errors": []
}


def save_report():
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

    print("\n========== GRAND_OPTIONS ==========")
    print(json.dumps(
        report["grand_options"],
        ensure_ascii=False,
        indent=2
    ))

    print("\n========== REPORT ==========")
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=2
    ))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    page = browser.new_page(
        locale="ja-JP"
    )

    page.set_default_timeout(20000)

    try:
        print("1. 打开竹之丸地区中心官网")

        page.goto(
            OFFICIAL_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(2000)

        print("官网页面：", page.url)

        print("2. 查找预约系统入口")

        reservation_links = page.locator(
            'a[href*="reserve1.jp"]'
        )

        if reservation_links.count() > 0:
            href = reservation_links.first.get_attribute("href")
            print("找到预约入口：", href)

            page.goto(
                href,
                wait_until="domcontentloaded",
                timeout=60000
            )
        else:
            print("没有找到直接链接，使用已确认的预约入口")
            page.goto(
                RESERVATION_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

        page.wait_for_timeout(2000)

        print("当前页面：", page.url)
        print("页面标题：", page.title())

        print("3. 点击空き照会・予約")

        availability = page.get_by_text(
            "空き照会・予約",
            exact=False
        ).first

        if availability.count() > 0:
            availability.click()
            page.wait_for_load_state(
                "domcontentloaded",
                timeout=30000
            )
            page.wait_for_timeout(2000)
        else:
            report["errors"].append(
                "没有找到「空き照会・予約」入口"
            )

        print("预约页面：", page.url)
        print("页面标题：", page.title())

        print("4. 读取 grand 的全部选项")

        grand = page.locator(
            'select[name="grand"]'
        )

        if grand.count() > 0:
            report["grand_options"] = (
                grand.locator("option").evaluate_all(
                    """
                    options => options.map(option => ({
                        value: option.value,
                        text: option.textContent.trim()
                    }))
                    """
                )
            )
        else:
            report["errors"].append(
                "当前页面没有找到 select[name='grand']"
            )

        print("5. 读取页面所有下拉选项")

        report["selects"] = page.locator(
            "select"
        ).evaluate_all(
            """
            selects => selects.map(select => ({
                name: select.name,
                value: select.value,
                options: Array.from(select.options).map(option => ({
                    value: option.value,
                    text: option.textContent.trim()
                }))
            }))
            """
        )

        print("6. 读取表单")

        report["forms"] = page.locator(
            "form"
        ).evaluate_all(
            """
            forms => forms.map(form => ({
                action: form.action,
                method: form.method,
                inputs: Array.from(
                    form.querySelectorAll(
                        "input, select, button"
                    )
                ).map(input => ({
                    name: input.name,
                    type: input.type,
                    value: input.value
                }))
            }))
            """
        )

        print("7. 读取页面文字")

        report["page_text"] = page.locator(
            "body"
        ).inner_text()[:15000]

        report["final_url"] = page.url
        report["page_title"] = page.title()

        print("诊断完成")

    except Exception as e:
        error = repr(e)
        print("发生错误：", error)
        report["errors"].append(error)

        report["final_url"] = page.url
        report["page_title"] = page.title()

    finally:
        save_report()
        browser.close()

if report["errors"]:
    sys.exit(1)
