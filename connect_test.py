
import json
import re
import traceback
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

# ============================================================
# 竹之丸地区中心：普通工作日预约表诊断
# 固定测试日期：2026-11-04
# 只读取网页，不发送 ntfy，不读取或更新 state.json
# ============================================================

OFFICIAL_URL = "https://www.nakakumin.com/takenomaruchikusen/"
TEST_DATE = "2026-11-04"
TEST_MONTH = "202611"

REPORT_DIR = Path("artifacts")
REPORT_DIR.mkdir(exist_ok=True)

REPORT_FILE = REPORT_DIR / "weekday_diagnostic_report.json"


def log(message):
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def wait_page(page):
    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=10000
        )
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(1500)


def enter_reservation(browser):
    """
    从竹之丸中心官网进入预约系统。
    必须通过官网表单打开预约系统，不直接访问登录入口。
    """

    context = browser.new_context(
        viewport={"width": 1440, "height": 1000}
    )

    page = context.new_page()

    log("1. 打开竹之丸地区中心官网")

    success = False

    for attempt in range(1, 4):
        try:
            log(f"官网连接尝试：{attempt}/3")

            response = page.goto(
                OFFICIAL_URL,
                wait_until="commit",
                timeout=45000
            )

            wait_page(page)

            log(f"官网页面：{page.url}")
            log(f"页面标题：{page.title()}")

            if response:
                log(f"官网 HTTP 状态：{response.status}")

            success = True
            break

        except Exception as e:
            log(f"官网连接失败：{e}")

            if attempt == 3:
                raise

            page.wait_for_timeout(3000)

    if not success:
        raise RuntimeError("无法打开竹之丸地区中心官网")

    log("2. 查找官网预约系统入口")

    forms = page.locator("form").evaluate_all("""
        forms => forms.map((form, index) => ({
            index: index,
            action: form.getAttribute("action"),
            target: form.getAttribute("target"),
            html: form.outerHTML.slice(0, 3000)
        }))
    """)

    target_form = None

    for item in forms:
        action = item.get("action") or ""

        if "VisitorLogin.php" in action:
            target_form = item
            break

    if not target_form:
        raise RuntimeError(
            "官网没有找到 VisitorLogin.php 预约入口表单"
        )

    log("找到官网预约入口表单")

    # 检查隐藏参数，避免打开错误入口
    form_locator = page.locator(
        "form[action*='VisitorLogin.php']"
    ).first

    hidden_values = form_locator.evaluate("""
        form => {
            const result = {};
            form.querySelectorAll("input").forEach(input => {
                if (input.name) {
                    result[input.name] = input.value;
                }
            });
            return result;
        }
    """)

    log(f"官网表单隐藏参数：{hidden_values}")

    if (
        hidden_values.get("mn") != "6"
        or hidden_values.get("lc") != "xnxqxcaqri"
        or hidden_values.get("gr") != "1"
    ):
        raise RuntimeError(
            "官网预约入口参数与已确认参数不一致，停止运行"
        )

    log("3. 通过官网表单打开预约系统")

    with page.expect_popup(timeout=20000) as popup_info:
        form_locator.evaluate("""
            form => form.submit()
        """)

    reservation_page = popup_info.value
    wait_page(reservation_page)

    log(f"预约入口页面：{reservation_page.url}")
    log(f"预约入口标题：{reservation_page.title()}")

    log("4. 选择空き状況からみる")

    reservation_page.locator(
        'input[name="authority"][value="空き状況からみる"]'
    ).click()

    wait_page(reservation_page)

    log(f"当前页面：{reservation_page.url}")

    log("5. 选择竹之丸地区中心体育室")

    reservation_page.locator(
        'select[name="grand"]'
    ).select_option("4")

    reservation_page.evaluate("""
        () => {
            const form = document.querySelector("form");
            if (form) {
                form.requestSubmit();
            }
        }
    """)

    reservation_page.locator(
        'select[name="Ym_select"]'
    ).wait_for(
        state="visible",
        timeout=30000
    )

    wait_page(reservation_page)

    log(f"体育室预约页面：{reservation_page.url}")
    log(f"页面标题：{reservation_page.title()}")

    return context, reservation_page


def get_months(page):
    return page.locator(
        'select[name="Ym_select"] option'
    ).evaluate_all("""
        options => options.map(option => ({
            value: option.value,
            text: option.textContent.trim()
        })).filter(item => item.value)
    """)


def switch_month(page, month):
    log(f"切换查询月份：{month}")

    # 优先使用已经验证过的月份切换函数
    page.evaluate("""
        month => {
            if (typeof SubmitFormM === "function") {
                SubmitFormM(month);
            } else {
                const select =
                    document.querySelector(
                        'select[name="Ym_select"]'
                    );

                if (!select) {
                    throw new Error("找不到月份选择框");
                }

                select.value = month;

                const form = select.closest("form");

                if (form) {
                    form.requestSubmit();
                }
            }
        }
    """, month)

    page.wait_for_function(
        """month => {
            const select =
                document.querySelector(
                    'select[name="Ym_select"]'
                );

            return select && select.value === month;
        }""",
        arg=month,
        timeout=20000
    )

    page.wait_for_timeout(1500)


def get_dates(page):
    html = page.content()

    dates = sorted(set(re.findall(
        r"SubmitFormD\(['\"](\d{4}-\d{2}-\d{2})['\"]\)",
        html
    )))

    return dates


def switch_date(page, date_text):
    log(f"准备查询日期：{date_text}")

    selector = f'input[onclick*="{date_text}"]'

    date_button = page.locator(selector).first

    if date_button.count() == 0:
        raise RuntimeError(
            f"日历中没有找到日期按钮：{date_text}"
        )

    before_caption = page.locator(
        "table.table_base caption"
    ).first.inner_text()

    log(f"切换前预约表标题：{before_caption.strip()}")

    try:
        with page.expect_navigation(
            wait_until="domcontentloaded",
            timeout=15000
        ):
            date_button.click()

        log("点击后检测到整页导航")

    except PlaywrightTimeoutError:
        log("点击后没有检测到标准导航，继续检查页面")

    # 等待预约表真正切换到目标日期
    page.wait_for_function(
        """dateText => {
            const caption =
                document.querySelector(
                    "table.table_base caption"
                );

            return caption &&
                   caption.innerText.includes(dateText);
        }""",
        arg=date_text,
        timeout=25000
    )

    page.wait_for_timeout(1500)

    log("日期切换成功")

    caption = page.locator(
        "table.table_base caption"
    ).first.inner_text()

    log(f"切换后预约表标题：{caption.strip()}")


def collect_page_images(page):
    """
    收集页面所有图片及其附近 HTML。
    用于确认 msg_icon01、msg_icon02 等图标的含义。
    """

    return page.locator("body").evaluate("""
        body => {
            return Array.from(body.querySelectorAll("img"))
                .map(img => ({
                    src: img.getAttribute("src"),
                    alt: img.getAttribute("alt"),
                    title: img.getAttribute("title"),
                    parent_text:
                        (img.parentElement?.innerText || "")
                            .trim()
                            .slice(0, 500),
                    parent_html:
                        (img.parentElement?.outerHTML || "")
                            .slice(0, 2000),
                    grandparent_text:
                        (img.parentElement?.parentElement?.innerText || "")
                            .trim()
                            .slice(0, 500),
                    grandparent_html:
                        (img.parentElement?.parentElement?.outerHTML || "")
                            .slice(0, 4000)
                }));
        }
    """)


def collect_legend_candidates(page):
    """
    收集状态图例附近的 HTML。
    不根据图标文件名猜测状态。
    """

    return page.locator("body").evaluate("""
        body => {
            const terms = [
                "選択可能",
                "空き",
                "予約済み",
                "選択不可",
                "利用不可",
                "電話対応",
                "保守"
            ];

            const elements = Array.from(
                body.querySelectorAll("td, th, span, div, li")
            );

            const results = [];
            const seen = new Set();

            for (const el of elements) {
                const text = (el.innerText || "").trim();

                if (!text || text.length > 500) {
                    continue;
                }

                if (!terms.some(term => text.includes(term))) {
                    continue;
                }

                const parent = el.parentElement;
                const html = parent
                    ? parent.outerHTML
                    : el.outerHTML;

                if (seen.has(html)) {
                    continue;
                }

                seen.add(html);

                results.push({
                    tag: el.tagName,
                    className:
                        typeof el.className === "string"
                            ? el.className
                            : "",
                    text: text,
                    html: html.slice(0, 5000)
                });

                if (results.length >= 40) {
                    break;
                }
            }

            return results;
        }
    """)


def diagnose(page):
    report = {
        "run_time": datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).isoformat(),
        "purpose": (
            "普通工作日预约表及状态图例诊断。"
            "不发送通知，不更新状态。"
        ),
        "test_date": TEST_DATE,
        "test_month": TEST_MONTH
    }

    log("6. 检查可查询月份")

    months = get_months(page)

    report["months"] = months

    log(f"可查询月份：{months}")

    available_months = [
        item["value"] for item in months
    ]

    if TEST_MONTH not in available_months:
        raise RuntimeError(
            f"当前预约页面没有 {TEST_MONTH} 月份"
        )

    log("7. 切换到2026年11月")

    switch_month(page, TEST_MONTH)

    dates = get_dates(page)

    report["november_dates"] = dates

    log(f"11月日历日期数量：{len(dates)}")

    if TEST_DATE not in dates:
        raise RuntimeError(
            f"日历中没有找到测试日期 {TEST_DATE}"
        )

    log("8. 切换到11月4日")

    switch_date(page, TEST_DATE)

    # 保存完整页面 HTML
    full_html = page.content()

    html_file = REPORT_DIR / "weekday_20261104.html"
    html_file.write_text(
        full_html,
        encoding="utf-8"
    )

    log(f"完整网页 HTML 已保存：{html_file}")

    # 保存预约表 HTML
    table_html = page.locator(
        "table.table_base"
    ).first.evaluate(
        "element => element.outerHTML"
    )

    table_file = REPORT_DIR / "weekday_table_20261104.html"
    table_file.write_text(
        table_html,
        encoding="utf-8"
    )

    report["table_html"] = table_html

    log(f"预约表 HTML 已保存：{table_file}")

    # 收集所有图片及附近结构
    report["page_images"] = collect_page_images(page)

    log(
        "页面图片数量："
        f"{len(report['page_images'])}"
    )

    # 收集状态图例候选区域
    report["legend_candidates"] = (
        collect_legend_candidates(page)
    )

    log(
        "状态图例候选区域数量："
        f"{len(report['legend_candidates'])}"
    )

    # 保存截图
    screenshot_file = (
        REPORT_DIR / "weekday_20261104.png"
    )

    page.screenshot(
        path=str(screenshot_file),
        full_page=True
    )

    log(f"页面截图已保存：{screenshot_file}")

    # 基础信息
    report["page_url"] = page.url
    report["page_title"] = page.title()
    report["table_count"] = page.locator(
        "table.table_base"
    ).count()

    report["body_text"] = page.locator(
        "body"
    ).inner_text()[:12000]

    report["html_length"] = len(full_html)

    return report


def main():
    report = {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True
            )

            context = None

            try:
                context, page = enter_reservation(browser)

                report = diagnose(page)

                report["success"] = True

            finally:
                if context:
                    context.close()

                browser.close()

    except Exception as e:
        report["success"] = False
        report["error"] = str(e)
        report["traceback"] = traceback.format_exc()

        log(f"诊断失败：{e}")
        log(traceback.format_exc())

    finally:
        report["run_time"] = datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).isoformat()

        REPORT_FILE.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        log(f"诊断报告已保存：{REPORT_FILE}")

        print("\n========== 诊断结果 ==========")
        print(json.dumps(
            report,
            ensure_ascii=False,
            indent=2
        ))
        print("========== 诊断结束 ==========")

    if not report.get("success"):
        raise RuntimeError("竹之丸普通工作日诊断失败")


if __name__ == "__main__":
    main()
