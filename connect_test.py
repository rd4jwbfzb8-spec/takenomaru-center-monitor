
import json
import os
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

OFFICIAL_URL = (
    "https://www.nakakumin.com/"
    "takenomaruchikusen/"
)

REPORT_DIR = "artifacts"
os.makedirs(REPORT_DIR, exist_ok=True)

report = {
    "test_time": datetime.now(timezone.utc).isoformat(),
    "official_url": OFFICIAL_URL,
    "reservation_url": "",
    "page_title": "",
    "grand_options": [],
    "selects": [],
    "forms": [],
    "page_text": "",
    "errors": []
}


def save_report():
    with open(
        os.path.join(REPORT_DIR, "report.json"),
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


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

        print("官网：", page.url)
        print("标题：", page.title())

        print("2. 找到官网预约表单")

        reservation_form = page.locator(
            'form[action*="VisitorLogin.php"]'
        ).first

        if reservation_form.count() == 0:
            raise Exception("没有找到官网预约表单")

        form_data = reservation_form.evaluate("""
            form => ({
                action: form.action,
                method: form.method,
                target: form.target,
                inputs: Array.from(form.querySelectorAll("input")).map(
                    input => ({
                        name: input.name,
                        value: input.value,
                        type: input.type
                    })
                )
            })
        """)

        print("预约表单：")
        print(json.dumps(form_data, ensure_ascii=False, indent=2))

        print("3. 通过官网表单提交预约请求")

        submit_button = reservation_form.locator(
            'input[type="submit"]'
        )

        with page.expect_popup(timeout=30000) as popup_info:
            submit_button.click()

        reservation_page = popup_info.value

        reservation_page.wait_for_load_state(
            "domcontentloaded",
            timeout=60000
        )
        reservation_page.wait_for_timeout(3000)

        report["reservation_url"] = reservation_page.url
        report["page_title"] = reservation_page.title()

        print("预约页面：", reservation_page.url)
        print("页面标题：", reservation_page.title())

        print("4. 读取 grand 全部选项")

        grand = reservation_page.locator(
            'select[name="grand"]'
        )

        if grand.count() > 0:
            report["grand_options"] = grand.locator(
                "option"
            ).evaluate_all("""
                options => options.map(option => ({
                    value: option.value,
                    text: option.textContent.trim(),
                    selected: option.selected
                }))
            """)
        else:
            report["errors"].append(
                "预约页面没有找到 select[name='grand']"
            )

        print("GRAND_OPTIONS:")
        print(json.dumps(
            report["grand_options"],
            ensure_ascii=False,
            indent=2
        ))

        print("5. 读取页面其他下拉选项")

        report["selects"] = reservation_page.locator(
            "select"
        ).evaluate_all("""
            selects => selects.map(select => ({
                name: select.name,
                value: select.value,
                options: Array.from(select.options).map(option => ({
                    value: option.value,
                    text: option.textContent.trim()
                }))
            }))
        """)

        print("6. 读取页面表单")

        report["forms"] = reservation_page.locator(
            "form"
        ).evaluate_all("""
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

        print("7. 读取页面文字")

        report["page_text"] = reservation_page.locator(
            "body"
        ).inner_text()[:12000]

        print("页面内容：")
        print(report["page_text"][:5000])

        print("诊断完成")

    except Exception as e:
        report["errors"].append(repr(e))
        print("发生错误：", repr(e))

    finally:
        save_report()
        print("报告已保存：artifacts/report.json")
        browser.close()
