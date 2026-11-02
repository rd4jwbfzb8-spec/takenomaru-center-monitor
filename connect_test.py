
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# =========================
# 单日诊断版设置
# =========================

OFFICIAL_URL = "https://www.nakakumin.com/takenomaruchikusen/"

REPORT_DIR = Path("artifacts")
REPORT_FILE = REPORT_DIR / "date_diagnostic_report.json"

JST = ZoneInfo("Asia/Tokyo")


def log(message):
    print(
        f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] "
        f"{message}",
        flush=True
    )


def save_report(report):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    log(f"诊断报告已保存：{REPORT_FILE}")


def save_debug(page, name):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        html = page.content()
        (REPORT_DIR / f"{name}.html").write_text(
            html,
            encoding="utf-8"
        )
        log(f"HTML 已保存：artifacts/{name}.html")
    except Exception as e:
        log(f"HTML 保存失败：{e}")

    try:
        page.screenshot(
            path=str(REPORT_DIR / f"{name}.png"),
            full_page=True,
            timeout=20000
        )
        log(f"截图已保存：artifacts/{name}.png")
    except Exception as e:
        log(f"截图保存失败：{e}")


def wait_page(page):
    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=10000
        )
    except PlaywrightTimeoutError:
        pass

    # 恢复之前成功版本的等待时间
    page.wait_for_timeout(2000)


# =========================
# 官网入口（保留已成功流程）
# =========================

def enter_reservation(browser):
    context = browser.new_context(
        locale="ja-JP",
        viewport={"width": 1440, "height": 1000}
    )

    page = context.new_page()
    page.set_default_timeout(20000)
    page.set_default_navigation_timeout(90000)

    log("1. 打开竹之丸地区中心官网")
    response = None

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
            break

        except Exception as e:
            log(f"官网连接失败：{e}")

            if attempt == 3:
                save_debug(page, "official_error")
                raise RuntimeError(
                    "官网连续三次无法打开"
                ) from e

            page.wait_for_timeout(3000)

    if response is not None:
        log(f"官网 HTTP 状态：{response.status}")

    log("2. 找到官网预约入口")

    forms = page.locator("form")
    entry_form = None

    for i in range(forms.count()):
        form = forms.nth(i)
        action = form.get_attribute("action") or ""
        inputs = form.locator("input")
        values = {}

        for j in range(inputs.count()):
            item = inputs.nth(j)
            name = item.get_attribute("name")
            value = item.get_attribute("value")

            if name:
                values[name] = value

        if (
            "VisitorLogin.php" in action
            and values.get("mn") == "6"
            and values.get("lc") == "xnxqxcaqri"
            and values.get("gr") == "1"
        ):
            entry_form = form
            break

    if entry_form is None:
        save_debug(page, "official_form_error")
        raise RuntimeError(
            "官网没有找到已确认的预约 POST 表单"
        )

    log("已确认官网预约表单及 POST 参数")
    log("3. 提交官网预约表单")

    try:
        with page.expect_popup(timeout=15000) as popup_info:
            entry_form.locator(
                'input[type="submit"]'
            ).first.click()

        popup = popup_info.value

    except PlaywrightTimeoutError:
        if len(context.pages) > 1:
            popup = context.pages[-1]
        else:
            save_debug(page, "popup_error")
            raise RuntimeError(
                "提交官网表单后没有打开预约系统窗口"
            )

    wait_page(popup)

    log(f"预约系统 URL：{popup.url}")
    log(f"预约系统标题：{popup.title()}")

    popup.wait_for_selector(
        'input[name="authority"]',
        timeout=30000
    )

    log("4. 点击空き状況からみる")

    authority = popup.locator(
        'input[name="authority"][value="空き状況からみる"]'
    )

    if authority.count() == 0:
        save_debug(popup, "authority_error")
        raise RuntimeError(
            "没有找到空き状況からみる按钮"
        )

    authority.click()
    wait_page(popup)

    log("5. 选择竹之丸地区中心体育室")

    grand = popup.locator('select[name="grand"]')
    grand.wait_for(state="visible")
    grand.select_option("4")

    grand.evaluate(
        """element => {
            if (!element.form) {
                throw new Error("设施选择表单不存在");
            }
            element.form.requestSubmit();
        }"""
    )

    wait_page(popup)

    popup.wait_for_selector(
        'select[name="Ym_select"]',
        timeout=30000
    )

    log(f"体育室预约页面：{popup.url}")
    log(f"体育室页面标题：{popup.title()}")

    return context, popup


# =========================
# 月份与日期读取
# =========================

def get_months(page):
    log("6. 读取可查询月份")

    options = page.locator(
        'select[name="Ym_select"] option'
    ).evaluate_all(
        "elements => elements.map(e => e.value)"
    )

    months = sorted({
        str(value)
        for value in options
        if re.fullmatch(r"\d{6}", str(value))
    })

    if not months:
        save_debug(page, "months_error")
        raise RuntimeError(
            "没有读取到可查询月份"
        )

    log(f"读取到月份：{months}")

    return months


def switch_month(page, month):
    log(f"切换到月份：{month}")

    has_function = page.evaluate(
        "() => typeof SubmitFormM === 'function'"
    )

    if has_function:
        page.evaluate(
            "(m) => SubmitFormM(m)",
            month
        )
    else:
        select = page.locator(
            'select[name="Ym_select"]'
        )
        select.select_option(month)
        select.evaluate(
            "element => element.form.requestSubmit()"
        )

    wait_page(page)

    actual_month = page.locator(
        'select[name="Ym_select"]'
    ).input_value()

    log(f"当前月份选择框值：{actual_month}")

    if actual_month != month:
        raise RuntimeError(
            f"月份切换失败：期望 {month}，实际 {actual_month}"
        )


def get_dates(page):
    onclicks = page.locator(
        '[onclick*="SubmitFormD("]'
    ).evaluate_all(
        "elements => elements.map(e => e.getAttribute('onclick'))"
    )

    dates = set()

    for onclick in onclicks:
        if not onclick:
            continue

        match = re.search(
            r"""SubmitFormD\(['"](\d{4}-\d{2}-\d{2})['"]\)""",
            onclick
        )

        if match:
            dates.add(match.group(1))

    return sorted(dates)


# =========================
# 日期诊断：点击真实日历按钮
# =========================

def diagnose_one_day(page, date_text, report):
    name = date_text.replace("-", "")

    date_value = datetime.strptime(
        date_text,
        "%Y-%m-%d"
    )

    expected_title = (
        f"{date_value.year}年"
        f"{date_value.month:02d}月"
        f"{date_value.day:02d}日"
    )

    log(f"7. 开始诊断日期：{date_text}")
    log(f"等待预约表标题：{expected_title}")

    before_html = page.content()

    report["test_date"] = date_text
    report["expected_title"] = expected_title

    report["before"] = {
        "url": page.url,
        "title": page.title(),
        "html_length": len(before_html),
        "table_count": page.locator(
            "table.table_base"
        ).count()
    }

    save_debug(page, f"before_{name}")

    # 使用日历上真实的日期按钮
    date_button = page.locator(
        f'input[onclick*="{date_text}"]'
    )

    if date_button.count() == 0:
        save_debug(page, f"date_button_error_{name}")
        raise RuntimeError(
            f"日历中没有找到日期按钮：{date_text}"
        )

    log("已找到日历日期按钮，准备点击")

    navigation_result = "未检测到整页导航"

    try:
        with page.expect_navigation(
            wait_until="domcontentloaded",
            timeout=12000
        ):
            date_button.first.click()

        navigation_result = "点击后检测到整页导航"

    except PlaywrightTimeoutError:
        navigation_result = (
            "点击后 12 秒内未检测到整页导航，继续检查预约表"
        )

    except Exception as e:
        report["click_error"] = str(e)
        navigation_result = f"点击或导航异常：{e}"

    log(f"页面跳转判断：{navigation_result}")

    # 等待每日预约表出现目标日期
    try:
        page.wait_for_function(
            """expected => {
                const tables = document.querySelectorAll(
                    "table.table_base"
                );

                return Array.from(tables).some(table =>
                    (table.innerText || "").includes(expected)
                );
            }""",
            arg=expected_title,
            timeout=20000
        )

        report["date_switch_success"] = True
        log(f"日期切换成功：预约表出现 {expected_title}")

    except PlaywrightTimeoutError:
        report["date_switch_success"] = False
        report["date_switch_error"] = (
            f"预约表在 20 秒内没有出现 {expected_title}"
        )
        log("日期切换尚未确认，保存当前页面供分析")

    page.wait_for_timeout(1000)

    after_html = page.content()

    try:
        page_text = page.locator(
            "body"
        ).inner_text(timeout=10000)
    except Exception as e:
        page_text = ""
        report["after_read_error"] = str(e)

    report["navigation_result"] = navigation_result

    report["after"] = {
        "url": page.url,
        "title": page.title(),
        "html_length": len(after_html),
        "html_changed": before_html != after_html,
        "table_count": page.locator(
            "table.table_base"
        ).count(),
        "target_title_found": expected_title in page_text,
        "body_text_preview": page_text[:5000]
    }

    log(f"日期查询后的 URL：{page.url}")
    log(f"日期查询后的标题：{page.title()}")
    log(
        f"查询前后 HTML 是否变化："
        f"{before_html != after_html}"
    )

    save_debug(page, f"after_{name}")
    save_report(report)

    log("单日诊断结束。本次不会查询其他日期。")
    log("本次不会发送 ntfy，也不会修改 state.json。")


# =========================
# 主程序
# =========================

def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now(JST).date()

    report = {
        "run_time": datetime.now(JST).isoformat(),
        "today": today.isoformat(),
        "purpose": "单日日期查询诊断，不发送通知，不更新状态",
        "months": [],
        "date_count": 0,
        "test_date": None,
        "errors": []
    }

    with sync_playwright() as p:
        browser = None
        page = None

        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox"]
            )

            context, page = enter_reservation(browser)

            months = get_months(page)
            report["months"] = months

            dates = set()

            for month in months:
                if month < today.strftime("%Y%m"):
                    continue

                switch_month(page, month)

                month_dates = get_dates(page)

                log(
                    f"{month} 找到 {len(month_dates)} 个日期"
                )

                for date_text in month_dates:
                    try:
                        date_value = datetime.strptime(
                            date_text,
                            "%Y-%m-%d"
                        ).date()

                        if date_value > today:
                            dates.add(date_text)

                    except ValueError:
                        pass

            dates = sorted(dates)
            report["date_count"] = len(dates)

            log(f"待查询未来日期总数：{len(dates)}")

            if not dates:
                raise RuntimeError(
                    "没有找到今天以后的日期"
                )

            # 优先测试明天，避免重复查询页面当前显示的日期
            tomorrow = (today + timedelta(days=1)).isoformat()

            if tomorrow in dates:
                test_date = tomorrow
            else:
                test_date = dates[0]

            log(f"本次唯一测试日期：{test_date}")

            test_month = (
                test_date[:4] + test_date[5:7]
            )

            current_month = page.locator(
                'select[name="Ym_select"]'
            ).input_value()

            if current_month != test_month:
                switch_month(page, test_month)

            diagnose_one_day(
                page,
                test_date,
                report
            )

        except Exception as e:
            report["fatal_error"] = str(e)
            report["errors"].append(str(e))

            if page is not None:
                save_debug(page, "fatal_error")

            save_report(report)

            log(f"运行失败：{e}")
            raise

        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception as e:
                    log(f"浏览器关闭异常：{e}")


if __name__ == "__main__":
    main()
