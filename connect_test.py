
import json
import os
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

OFFICIAL_URL = "https://www.nakakumin.com/takenomaruchikusen/"

os.makedirs("artifacts", exist_ok=True)

report = {
    "test_time": datetime.now(timezone.utc).isoformat(),
    "reservation_url": "",
    "page_title": "",
    "grand_options": [],
    "selects": [],
    "forms": [],
    "page_text": "",
    "errors": []
}


def read_page(page):
    report["reservation_url"] = page.url
    report["page_title"] = page.title()

    report["grand_options"] = page.locator(
        'select[name="grand"] option'
    ).evaluate_all("""
        options => options.map(option => ({
            value: option.value,
            text: option.textContent.trim(),
            selected: option.selected
        }))
    """)

    report["selects"] = page.locator("select").evaluate_all("""
        selects => selects.map(select => ({
            name: select.name,
            value: select.value,
            options: Array.from(select.options).map(option => ({
                value: option.value,
                text: option.textContent.trim()
            }))
        }))
    """)

    report["forms"] = page.locator("form").evaluate_all("""
        forms => forms.map(form => ({
            action: form.action,
            method: form.method,
            inputs: Array.from(
                form.querySelectorAll("input, select")
            ).map(input => ({
                name: input.name,
                type: input.type,
                value: input.value
            }))
        }))
    """)

    report["page_text"] = page.locator("body").inner_text()[:12000]


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(locale="ja-JP")
    page.set_default_timeout(30000)

    try:
        print("1. 打开竹之丸官网")

        page.goto(
            OFFICIAL_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )
        page.wait_for_timeout(2000)

        print("2. 找到官网预约表单")

        form = page.locator(
            'form[action*="VisitorLogin.php"]'
        ).first

        if form.count() == 0:
            raise Exception("没有找到官网预约表单")

        print("3. 提交官网预约表单")

        submit = form.locator(
            'input[type="submit"]'
        ).first

        with page.expect_popup(timeout=30000) as popup_info:
            submit.click()

        reservation_page = popup_info.value
        reservation_page.wait_for_load_state(
            "domcontentloaded",
            timeout=60000
        )
        reservation_page.wait_for_timeout(2000)

        print("预约页面：", reservation_page.url)
        print("页面标题：", reservation_page.title())
        print("4. 点击空き状況からみる")

        availability_button = reservation_page.locator(
            'input[name="authority"][value="空き状況からみる"]'
        )

        if availability_button.count() == 0:
            raise Exception("没有找到「空き状況からみる」按钮")

        with reservation_page.expect_navigation(
            wait_until="domcontentloaded",
            timeout=30000
        ):
            availability_button.click()

        reservation_page.wait_for_timeout(2000)

        print("点击后页面：", reservation_page.url)
        print("点击后标题：", reservation_page.title())

        print("5. 读取场地选择器")

        read_page(reservation_page)

        print("GRAND_OPTIONS:")
        print(json.dumps(
            report["grand_options"],
            ensure_ascii=False,
            indent=2
        ))

        print("6. 页面文字")
        print(report["page_text"][:5000])

        print("7. 表单信息")
        print(json.dumps(
            report["forms"],
            ensure_ascii=False,
            indent=2
        ))

        print("诊断完成")

    except Exception as e:
        report["errors"].append(repr(e))
        print("发生错误：", repr(e))

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
        print("ERRORS:", report["errors"])
        browser.close()
