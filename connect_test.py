
import json
import os
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

OFFICIAL_URL = "https://www.nakakumin.com/takenomaruchikusen/"
OUTPUT_FILE = "artifacts/report.json"


def main():
    os.makedirs("artifacts", exist_ok=True)
    report = {
        "test_time": datetime.now(timezone.utc).isoformat(),
        "errors": []
    }

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(OFFICIAL_URL, wait_until="domcontentloaded", timeout=60000)

            # 通过官网表单进入预约系统
            with context.expect_page(timeout=30000) as popup_info:
                page.locator(
                    'form[action*="VisitorLogin.php"] input[type="submit"]'
                ).first.click()

            reservation_page = popup_info.value
            reservation_page.wait_for_load_state("domcontentloaded")

            # 进入「空き状況からみる」
            reservation_page.locator(
                'input[name="authority"][value="空き状況からみる"]'
            ).click()

            reservation_page.wait_for_load_state("domcontentloaded")
            reservation_page.wait_for_timeout(1500)

            # 切换到竹之丸地区センター（体育室）
            reservation_page.locator('select[name="grand"]').select_option("4")
            reservation_page.locator('select[name="grand"]').evaluate(
                "(el) => el.form.submit()"
            )

            reservation_page.wait_for_load_state("domcontentloaded")
            reservation_page.wait_for_timeout(2000)

            report["reservation_url"] = reservation_page.url
            report["page_title"] = reservation_page.title()
            report["selected_grand"] = reservation_page.locator(
                'select[name="grand"]'
            ).input_value()

            report["page_text"] = reservation_page.locator("body").inner_text()

            # 读取页面上的表格
            report["tables"] = reservation_page.locator("table").evaluate_all(
                """tables => tables.map(table => ({
                    text: table.innerText,
                    html: table.outerHTML
                }))"""
            )

            # 读取页面上所有可见选项
            report["selects"] = reservation_page.locator("select").evaluate_all(
                """els => els.map(el => ({
                    name: el.name,
                    value: el.value,
                    options: Array.from(el.options).map(o => ({
                        value: o.value,
                        text: o.textContent.trim()
                    }))
                }))"""
            )

        except Exception as e:
            report["errors"].append(str(e))

        finally:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)

            print("========== TAKENOMARU TEST REPORT ==========")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            browser.close()


if __name__ == "__main__":
    main()
