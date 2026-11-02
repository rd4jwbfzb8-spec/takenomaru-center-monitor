
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
# 竹之丸地区中心：单日日期查询诊断
# 不发送 ntfy，不读取或更新 state.json
# ============================================================

OFFICIAL_URL = "https://www.nakakumin.com/takenomaruchikusen/"
TEST_DATE = "2026-11-04"
TEST_MONTH = "202611"

REPORT_DIR = Path("artifacts")
REPORT_FILE = REPORT_DIR / "weekday_diagnostic_report.json"
JST = ZoneInfo("Asia/Tokyo")

FAILURE_DIAGNOSTICS = {}


def log(message):
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def wait_page(page):
    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=10000
        )
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(1500)


def save_page_files(page, prefix):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    html_path = REPORT_DIR / f"{prefix}.html"
    screenshot_path = REPORT_DIR / f"{prefix}.png"

    try:
        html_path.write_text(page.content(), encoding="utf-8")
        log(f"已保存页面HTML：{html_path}")
    except Exception as e:
        log(f"保存HTML失败：{e}")

    try:
        page.screenshot(
            path=str(screenshot_path),
            full_page=True
        )
        log(f"已保存页面截图：{screenshot_path}")
    except Exception as e:
        log(f"保存截图失败：{e}")

    return {
        "html_file": str(html_path),
        "screenshot_file": str(screenshot_path),
    }


def get_page_info(page):
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception as e:
        body_text = f"读取正文失败：{e}"

    try:
        caption = page.locator(
            "table.table_base caption"
        ).first.inner_text(timeout=3000)
    except Exception:
        caption = ""

    try:
        html_length = len(page.content())
    except Exception:
        html_length = 0

    return {
        "url": page.url,
        "title": page.title(),
        "caption": caption,
        "body_text_preview": body_text[:3000],
        "html_length": html_length,
        "table_count": page.locator("table.table_base").count(),
    }


# ============================================================
# 1. 进入预约系统
# ============================================================

def enter_reservation(browser):
    log("1. 打开竹之丸地区中心官网")

    page = browser.new_page()
    success = False

    for attempt in range(1, 4):
        try:
            log(f"官网连接尝试：{attempt}/3")
            page.goto(
                OFFICIAL_URL,
                wait_until="commit",
                timeout=45000
            )
            success = True
            break
        except Exception as e:
            log(f"官网连接失败：{e}")
            if attempt < 3:
                page.wait_for_timeout(3000)

    if not success:
        raise RuntimeError("连续3次无法打开竹之丸官网")

    wait_page(page)

    log(f"官网页面：{page.url}")
    log(f"官网标题：{page.title()}")

    form_index = page.locator("form").evaluate_all(
        """
        forms => forms.findIndex(form =>
            (form.getAttribute("action") || "")
                .includes("VisitorLogin.php")
        )
        """
    )

    if form_index < 0:
        save_page_files(page, "official_form_not_found")
        raise RuntimeError(
            "官网没有找到提交到 VisitorLogin.php 的表单"
        )

    official_form = page.locator("form").nth(form_index)

    form_info = official_form.evaluate(
        """
        form => {
            const values = {};
            form.querySelectorAll(
                'input[type="hidden"]'
            ).forEach(input => {
                values[input.name] = input.value;
            });

            return {
                action: form.action,
                method: form.method,
                target: form.target,
                hidden_values: values
            };
        }
        """
    )

    log(
        "官网表单信息："
        + json.dumps(form_info, ensure_ascii=False)
    )

    hidden = form_info.get("hidden_values", {})

    if hidden.get("mn") != "6":
        raise RuntimeError("官网表单参数 mn 不符合预期")

    if hidden.get("lc") != "xnxqxcaqri":
        raise RuntimeError("官网表单参数 lc 不符合预期")

    if hidden.get("gr") != "1":
        raise RuntimeError("官网表单参数 gr 不符合预期")

    log("2. 提交官网预约入口")

    try:
        with page.expect_popup(timeout=20000) as popup_info:
            official_form.evaluate("form => form.submit()")

        reservation_page = popup_info.value

    except PlaywrightTimeoutError:
        save_page_files(page, "official_popup_failed")
        raise RuntimeError(
            "提交官网表单后没有检测到预约系统弹窗"
        )

    wait_page(reservation_page)

    log(f"预约入口页面：{reservation_page.url}")
    log(f"预约入口标题：{reservation_page.title()}")

    log("3. 点击空き状況からみる")

    authority = reservation_page.locator(
        'input[name="authority"][value="空き状況からみる"]'
    )

    authority.wait_for(state="visible", timeout=20000)
    authority.click()

    wait_page(reservation_page)

    log("4. 选择竹之丸地区中心（体育室）")

    facility_select = reservation_page.locator(
        'select[name="grand"]'
    )

    facility_select.wait_for(state="visible", timeout=20000)
    facility_select.select_option("4")

    form_info = facility_select.evaluate(
        """
        select => {
            const form = select.form;

            return {
                form_exists: !!form,
                action: form ? form.action : null,
                method: form ? form.method : null,
                submitters: form
                    ? Array.from(
                        form.querySelectorAll(
                            'input[type="submit"], ' +
                            'input[type="image"], button'
                        )
                    ).map(button => ({
                        type: button.type,
                        name: button.name,
                        value: button.value
                    }))
                    : []
            };
        }
        """
    )

    log(
        "设施表单信息："
        + json.dumps(form_info, ensure_ascii=False)
    )

    log("5. 提交设施选择")

    # 必须提交 grand select 自己所属的表单
    try:
        with reservation_page.expect_navigation(
            wait_until="domcontentloaded",
            timeout=20000
        ):
            facility_select.evaluate(
                """
                select => {
                    const form = select.form;

                    if (!form) {
                        throw new Error(
                            "grand select 没有所属表单"
                        );
                    }

                    const submitter = form.querySelector(
                        'input[type="submit"], ' +
                        'input[type="image"], ' +
                        'button[type="submit"], ' +
                        'button:not([type])'
                    );

                    if (submitter) {
                        submitter.click();
                    } else {
                        form.requestSubmit();
                    }
                }
                """
            )

    except PlaywrightTimeoutError:
        log("设施提交后未检测到标准导航，继续检查页面")

    wait_page(reservation_page)

    log("6. 等待预约月份选择框")

    try:
        reservation_page.locator(
            'select[name="Ym_select"]'
        ).wait_for(
            state="visible",
            timeout=30000
        )
    except Exception:
        save_page_files(
            reservation_page,
            "facility_select_failed"
        )
        raise RuntimeError(
            "设施提交后没有出现预约月份选择框"
        )

    log("预约系统进入成功")
    log(f"预约页面：{reservation_page.url}")
    log(f"预约页面标题：{reservation_page.title()}")

    return reservation_page


# ============================================================
# 2. 读取可查询月份
# ============================================================

def get_months(page):
    month_select = page.locator(
        'select[name="Ym_select"]'
    )

    return month_select.locator("option").evaluate_all(
        """
        options => options.map(option => ({
            value: option.value,
            text: option.innerText.trim()
        })).filter(option => option.value)
        """
    )


# ============================================================
# 3. 切换月份
# ============================================================

def switch_month(page, month):
    log(f"切换查询月份：{month}")

    page.evaluate(
        """
        month => {
            if (typeof SubmitFormM === "function") {
                SubmitFormM(month);
                return;
            }

            const select = document.querySelector(
                'select[name="Ym_select"]'
            );

            if (!select) {
                throw new Error(
                    "找不到 Ym_select 月份选择框"
                );
            }

            select.value = month;

            if (!select.form) {
                throw new Error(
                    "月份选择框没有所属表单"
                );
            }

            select.form.requestSubmit();
        }
        """,
        month
    )

    page.wait_for_function(
        """
        month => {
            const select = document.querySelector(
                'select[name="Ym_select"]'
            );

            return select && select.value === month;
        }
        """,
        arg=month,
        timeout=25000
    )

    wait_page(page)
    log(f"月份切换完成：{month}")


# ============================================================
# 4. 读取日历日期
# ============================================================

def get_dates(page):
    html = page.content()

    dates = re.findall(
        r"""SubmitFormD\(['"](\d{4}-\d{2}-\d{2})['"]\)""",
        html
    )

    return list(dict.fromkeys(dates))


# ============================================================
# 5. 日期格式转换
# ============================================================

def to_japanese_date(date_text):
    """
    输入：2026-11-04
    输出：2026年11月04日

    网页标题后面可能还有 (水) 等星期信息，
    所以只匹配年月日部分。
    """
    match = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})",
        date_text
    )

    if not match:
        raise ValueError(
            f"日期格式不正确：{date_text}"
        )

    year, month, day = match.groups()

    return f"{year}年{month}月{day}日"


# ============================================================
# 6. 切换日期
# ============================================================

def switch_date(page, date_text):
    global FAILURE_DIAGNOSTICS

    log(f"准备查询日期：{date_text}")

    # 将程序日期转换为网页显示格式
    display_date = to_japanese_date(date_text)

    log(f"网页日期匹配格式：{display_date}")

    before_info = get_page_info(page)

    log(
        "切换前预约表标题："
        + before_info.get("caption", "")
    )

    function_exists = page.evaluate(
        "() => typeof SubmitFormD === 'function'"
    )

    if not function_exists:
        FAILURE_DIAGNOSTICS = {
            "step": "check_SubmitFormD",
            "date": date_text,
            "before": before_info,
            "error": "预约页面没有 SubmitFormD 函数",
        }

        save_page_files(
            page,
            "date_switch_function_missing"
        )

        raise RuntimeError(
            "预约页面没有找到 SubmitFormD 函数"
        )

    log("已确认页面存在 SubmitFormD 函数")

    # 直接调用网页原本的日期切换函数
    try:
        with page.expect_navigation(
            wait_until="domcontentloaded",
            timeout=15000
        ):
            page.evaluate(
                """
                dateText => {
                    SubmitFormD(dateText);
                }
                """,
                date_text
            )

        log("调用 SubmitFormD 后检测到整页导航")

    except PlaywrightTimeoutError:
        log(
            "调用 SubmitFormD 后未检测到标准导航，"
            "继续等待预约表更新"
        )

    except Exception as e:
        log(f"调用 SubmitFormD 时出现异常：{e}")

        FAILURE_DIAGNOSTICS = {
            "step": "call_SubmitFormD",
            "date": date_text,
            "before": before_info,
            "error": str(e),
        }

        save_page_files(
            page,
            "date_switch_call_error"
        )

        raise

    # 关键修正：
    # 不再用 2026-11-04 去匹配网页标题。
    # 改用 2026年11月04日。
    try:
        page.wait_for_function(
            """
            displayDate => {
                const caption = document.querySelector(
                    "table.table_base caption"
                );

                return caption &&
                    caption.innerText.includes(displayDate);
            }
            """,
            arg=display_date,
            timeout=25000
        )

    except PlaywrightTimeoutError:
        after_info = get_page_info(page)

        files = save_page_files(
            page,
            "date_switch_failure"
        )

        FAILURE_DIAGNOSTICS = {
            "step": "wait_target_date_caption",
            "target_date": date_text,
            "expected_display_date": display_date,
            "before": before_info,
            "after": after_info,
            "saved_files": files,
            "error": (
                "预约表标题在等待时间内没有出现目标日期"
            ),
        }

        log(
            "日期切换失败后的页面信息："
            + json.dumps(
                FAILURE_DIAGNOSTICS,
                ensure_ascii=False
            )
        )

        raise RuntimeError(
            "日期切换后预约表没有显示目标日期。"
            "已保存失败页面HTML和截图。"
        )

    wait_page(page)

    # 再读取一次标题，做第二次确认
    after_info = get_page_info(page)
    final_caption = after_info.get("caption", "")

    if display_date not in final_caption:
        FAILURE_DIAGNOSTICS = {
            "step": "final_caption_check",
            "target_date": date_text,
            "expected_display_date": display_date,
            "actual_caption": final_caption,
        }

        save_page_files(
            page,
            "date_switch_final_check_failed"
        )

        raise RuntimeError(
            "日期等待条件通过，但最终标题复核失败"
        )

    log("日期切换成功")
    log(f"预约表标题：{final_caption}")

    return {
        "success": True,
        "target_date": date_text,
        "expected_display_date": display_date,
        "actual_caption": final_caption,
        "before": before_info,
        "after": after_info,
    }


# ============================================================
# 7. 收集页面图片信息
# ============================================================

def collect_page_images(page):
    return page.locator("img").evaluate_all(
        """
        images => images.map(img => ({
            src: img.getAttribute("src"),
            alt: img.getAttribute("alt"),
            title: img.getAttribute("title"),
            parent_text: img.parentElement
                ? img.parentElement.innerText
                : "",
            parent_html: img.parentElement
                ? img.parentElement.outerHTML
                : "",
            grandparent_text: img.parentElement &&
                img.parentElement.parentElement
                ? img.parentElement.parentElement.innerText
                : "",
            grandparent_html: img.parentElement &&
                img.parentElement.parentElement
                ? img.parentElement.parentElement.outerHTML
                : ""
        }))
        """
    )


# ============================================================
# 8. 收集状态说明/图例候选内容
# ============================================================

def collect_legend_candidates(page):
    return page.locator(
        "td, th, span, div, li"
    ).evaluate_all(
        """
        elements => {
            const keywords = [
                "選択可能",
                "空き",
                "予約済み",
                "選択不可",
                "利用不可",
                "電話対応",
                "保守"
            ];

            return elements
                .map(element => ({
                    tag: element.tagName,
                    text: (element.innerText || "").trim(),
                    html: element.outerHTML,
                    parent_html: element.parentElement
                        ? element.parentElement.outerHTML
                        : ""
                }))
                .filter(item =>
                    item.text &&
                    item.text.length < 500 &&
                    keywords.some(keyword =>
                        item.text.includes(keyword)
                    )
                );
        }
        """
    )


# ============================================================
# 9. 单日诊断
# ============================================================

def diagnose(page):
    log("开始单日预约表诊断")

    months = get_months(page)

    log(
        "可查询月份："
        + json.dumps(months, ensure_ascii=False)
    )

    month_values = [
        item["value"] for item in months
    ]

    if TEST_MONTH not in month_values:
        raise RuntimeError(
            f"预约系统没有提供测试月份：{TEST_MONTH}"
        )

    log("7. 切换到2026年11月")
    switch_month(page, TEST_MONTH)

    dates = get_dates(page)

    log(f"11月日历日期数量：{len(dates)}")

    if not dates:
        save_page_files(
            page,
            "calendar_dates_not_found"
        )

        raise RuntimeError(
            "没有从11月日历中读取到日期"
        )

    if TEST_DATE not in dates:
        raise RuntimeError(
            f"日历中没有找到测试日期：{TEST_DATE}"
        )

    log(f"8. 切换到{TEST_DATE}")

    date_result = switch_date(page, TEST_DATE)

    log("9. 收集目标日期预约表")

    final_html = page.content()

    try:
        table_html = page.locator(
            "table.table_base"
        ).first.evaluate(
            "table => table.outerHTML"
        )
    except Exception:
        table_html = ""

    try:
        body_text = page.locator("body").inner_text()
    except Exception:
        body_text = ""

    page_images = collect_page_images(page)
    legend_candidates = collect_legend_candidates(page)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    html_file = REPORT_DIR / "target_date_full_page.html"
    html_file.write_text(final_html, encoding="utf-8")

    screenshot_file = REPORT_DIR / "target_date_screenshot.png"

    page.screenshot(
        path=str(screenshot_file),
        full_page=True
    )

    report = {
        "run_time": datetime.now(JST).isoformat(),
        "today": datetime.now(JST).date().isoformat(),
        "purpose": (
            "单日日期查询诊断，附带预约表完整HTML，"
            "不发送通知，不读取或更新state.json"
        ),
        "months": month_values,
        "month_options": months,
        "date_count": len(dates),
        "test_date": TEST_DATE,
        "date_switch": date_result,
        "final_page": get_page_info(page),
        "body_text_preview": body_text[:5000],
        "table_html": table_html,
        "page_images": page_images,
        "legend_candidates": legend_candidates,
        "html_file": str(html_file),
        "screenshot_file": str(screenshot_file),
    }

    log("单日诊断完成")

    return report


# ============================================================
# 10. 主程序
# ============================================================

def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        "run_time": datetime.now(JST).isoformat(),
        "today": datetime.now(JST).date().isoformat(),
        "test_date": TEST_DATE,
        "success": False,
    }

    browser = None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True
            )

            page = enter_reservation(browser)

            result = diagnose(page)

            report.update(result)
            report["success"] = True

            browser.close()
            browser = None

    except Exception as e:
        report["success"] = False
        report["error"] = str(e)
        report["traceback"] = traceback.format_exc()

        if FAILURE_DIAGNOSTICS:
            report["failure_diagnostics"] = (
                FAILURE_DIAGNOSTICS
            )

        log(f"诊断失败：{e}")
        log(traceback.format_exc())

        if browser:
            try:
                browser.close()
            except Exception:
                pass

    finally:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)

        REPORT_FILE.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        print("\n========== 诊断报告 ==========")

        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2
            )
        )

        print("==============================")

        log(f"报告已保存：{REPORT_FILE}")

    if not report.get("success"):
        raise RuntimeError(
            "诊断未成功，请查看上方错误和诊断报告"
        )


if __name__ == "__main__":
    main()
