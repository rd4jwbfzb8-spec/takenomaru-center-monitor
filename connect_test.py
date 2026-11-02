
import json
import re
import time
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from urllib.error import URLError

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# =========================
# 基本设置
# =========================

OFFICIAL_URL = "https://www.nakakumin.com/takenomaruchikusen/"

NTFY_TOPIC = "minami-rsv-k8p4x7q2m9"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

STATE_FILE = Path("state.json")
REPORT_DIR = Path("artifacts")
REPORT_FILE = REPORT_DIR / "report.json"

ROOMS = {
    "体育室（Ａ）": "A",
    "体育室（Ｂ）": "B",
    "体育室（Ｃ）": "C",
}

TIME_SLOTS = [
    "09:00-12:00",
    "12:00-15:00",
    "15:00-18:00",
    "18:00-21:00",
]

JST = ZoneInfo("Asia/Tokyo")


# =========================
# 通用辅助函数
# =========================

def log(message):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {message}")


def save_report(data):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def save_debug_page(page, name="debug"):
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)

        (REPORT_DIR / f"{name}.html").write_text(
            page.content(),
            encoding="utf-8"
        )

        page.screenshot(
            path=str(REPORT_DIR / f"{name}.png"),
            full_page=True
        )

        log(f"已保存诊断页面：artifacts/{name}.html")
    except Exception as e:
        log(f"保存诊断页面失败：{e}")


def wait_page(page, seconds=2):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass

    page.wait_for_timeout(seconds * 1000)


def submit_form(page, selector):
    """
    提交指定元素所在的表单。
    不依赖 expect_navigation，兼容页面普通提交及部分异步加载情况。
    """
    page.locator(selector).evaluate(
        """element => {
            if (!element.form) {
                throw new Error("找不到元素所属的表单");
            }
            element.form.requestSubmit();
        }"""
    )
    wait_page(page, 2)


# =========================
# 进入预约系统
# =========================

def open_reservation_page(browser):
    log("1. 打开竹之丸地区中心官网")

    context = browser.new_context(
        viewport={"width": 1440, "height": 1000},
        locale="ja-JP"
    )

    page = context.new_page()
    page.goto(
        OFFICIAL_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )

    wait_page(page, 2)

    log(f"官网页面：{page.url}")
    log(f"页面标题：{page.title()}")

    log("2. 点击官网预约系统入口")

    # 官网预约入口会通过 POST 参数打开预约系统
    entry = page.locator(
        'input[type="submit"][value*="施設予約"]'
    ).first

    if entry.count() == 0:
        save_debug_page(page, "official_page_error")
        raise RuntimeError("官网没有找到设施预约入口")

    popup = None

    try:
        with page.expect_popup(timeout=15000) as popup_info:
            entry.click()

        popup = popup_info.value

    except PlaywrightTimeoutError:
        # 某些页面可能不会打开新窗口
        if len(context.pages) > 1:
            popup = context.pages[-1]
        else:
            popup = page

    wait_page(popup, 2)

    log(f"预约入口页面：{popup.url}")
    log(f"预约入口标题：{popup.title()}")

    # 选择「空き状況からみる」
    log("3. 选择空き状況からみる")

    authority = popup.locator(
        'input[name="authority"][value="空き状況からみる"]'
    )

    if authority.count() == 0:
        save_debug_page(popup, "authority_error")
        raise RuntimeError("没有找到「空き状況からみる」按钮")

    authority.click()
    wait_page(popup, 2)

    log(f"当前页面：{popup.url}")
    log(f"当前标题：{popup.title()}")

    # 选择竹之丸地区中心体育室
    log("4. 选择竹之丸地区中心（体育室）")

    grand = popup.locator('select[name="grand"]')

    if grand.count() == 0:
        save_debug_page(popup, "grand_error")
        raise RuntimeError("没有找到设施选择下拉框 grand")

    grand.select_option("4")

    submit_form(popup, 'select[name="grand"]')

    log(f"体育室预约页面：{popup.url}")
    log(f"体育室页面标题：{popup.title()}")

    # 等待月份选择框出现
    try:
        popup.wait_for_selector(
            'select[name="Ym_select"]',
            timeout=20000
        )
    except PlaywrightTimeoutError:
        save_debug_page(popup, "month_select_error")

        raise RuntimeError(
            "进入体育室页面后，20秒内没有找到月份选择框。"
            f" 当前标题：{popup.title()}；当前URL：{popup.url}"
        )

    return context, popup


# =========================
# 读取可查询月份
# =========================

def get_months(page):
    log("5. 读取可查询月份")

    selector = page.locator('select[name="Ym_select"]')

    if selector.count() == 0:
        raise RuntimeError("月份下拉框不存在")

    options = selector.locator("option").evaluate_all(
        """elements => elements.map(e => ({
            value: e.value,
            text: e.textContent.trim()
        }))"""
    )

    months = []

    for option in options:
        value = str(option["value"]).strip()

        if re.fullmatch(r"\d{6}", value):
            months.append(value)

    months = sorted(set(months))

    if not months:
        save_debug_page(page, "months_empty")
        raise RuntimeError(
            "找到了月份下拉框，但没有读取到有效月份。"
        )

    log(f"可查询月份：{months}")

    return months


def switch_month(page, month):
    log(f"切换月份：{month}")

    # 优先使用预约系统自带的月份切换函数
    has_function = page.evaluate(
        "() => typeof SubmitFormM === 'function'"
    )

    if has_function:
        page.evaluate(
            "(month) => SubmitFormM(month)",
            month
        )
    else:
        # 如果页面没有该函数，则通过下拉框提交
        selector = page.locator('select[name="Ym_select"]')
        selector.select_option(month)
        submit_form(page, 'select[name="Ym_select"]')

    wait_page(page, 2)


# =========================
# 读取月份中的日期
# =========================

def get_dates(page):
    elements = page.locator(
        '[onclick*="SubmitFormD("]'
    ).evaluate_all(
        """elements => elements.map(e => e.getAttribute("onclick"))"""
    )

    dates = set()

    for onclick in elements:
        if not onclick:
            continue

        match = re.search(
            r"SubmitFormD\\(['\"](\\d{4}-\\d{2}-\\d{2})['\"]\\)",
            onclick
        )

        if match:
            dates.add(match.group(1))

    return sorted(dates)


# =========================
# 查询某一天
# =========================

def open_day(page, date_text):
    log(f"查询日期：{date_text}")

    has_function = page.evaluate(
        "() => typeof SubmitFormD === 'function'"
    )

    if not has_function:
        raise RuntimeError("预约页面没有找到 SubmitFormD 函数")

    page.evaluate(
        "(date) => SubmitFormD(date)",
        date_text
    )

    wait_page(page, 2)

    # 等待预约状况表出现
    page.wait_for_selector(
        "table.table_base",
        timeout=20000
    )


# =========================
# 判断单元格是否真的可以预约
# =========================

def cell_is_available(cell):
    """
    只有存在未禁用的复选框才算可预约。
    不把「－」、预约图标、不可选状态当作空位。
    """

    checkboxes = cell.locator(
        'input[type="checkbox"]'
    )

    count = checkboxes.count()

    if count == 0:
        return False

    for i in range(count):
        checkbox = checkboxes.nth(i)

        try:
            if checkbox.is_enabled():
                return True
        except Exception:
            continue

    return False


# =========================
# 读取当天 A/B/C 空位
# =========================

def read_daily_availability(page, date_text):
    table = page.locator("table.table_base").last

    if table.count() == 0:
        raise RuntimeError(f"{date_text} 没有找到预约状况表")

    rows = table.locator("tr")
    available = []

    for i in range(rows.count()):
        row = rows.nth(i)
        cells = row.locator("td")

        if cells.count() < 5:
            continue

        room_name = re.sub(
            r"\s+",
            "",
            cells.nth(0).inner_text()
        )

        room_code = None

        for room, code in ROOMS.items():
            if room in room_name:
                room_code = code
                break

        if not room_code:
            continue

        for slot_index in range(4):
            cell = cells.nth(slot_index + 1)

            if cell_is_available(cell):
                available.append({
                    "date": date_text,
                    "room": room_code,
                    "time": TIME_SLOTS[slot_index]
                })

    return available


# =========================
# ntfy 通知
# =========================

def send_ntfy(items):
    if not items:
        return

    lines = ["竹之丸地区中心 体育室有新空位！"]

    for item in items:
        lines.append(
            f"{item['date']}  体育室{item['room']}  {item['time']}"
        )

    message = "\n".join(lines)

    request = Request(
        NTFY_URL,
        data=message.encode("utf-8"),
        headers={
            "Title": "竹之丸地区中心空位通知",
            "Priority": "high",
            "Tags": "badminton,calendar"
        },
        method="POST"
    )

    try:
        with urlopen(request, timeout=20) as response:
            log(f"ntfy 通知发送成功：HTTP {response.status}")

    except (URLError, TimeoutError) as e:
        log(f"ntfy 通知发送失败：{e}")


# =========================
# 状态记录
# =========================

def load_state():
    if not STATE_FILE.exists():
        return set()

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        return {
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            for item in data
        }

    except Exception as e:
        log(f"读取 state.json 失败，将重新建立状态：{e}")
        return set()


def save_state(items):
    STATE_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def item_key(item):
    return json.dumps(
        item,
        ensure_ascii=False,
        sort_keys=True
    )


# =========================
# 主程序
# =========================

def main():
    today = datetime.now(JST).date()

    report = {
        "run_time": datetime.now(JST).isoformat(),
        "today": today.isoformat(),
        "months": [],
        "date_count": 0,
        "available_count": 0,
        "available": [],
        "errors": []
    }

    browser = None
    context = None

    try:
        with sync_playwright() as playwright:

            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox"]
            )

            context, page = open_reservation_page(browser)

            months = get_months(page)
            report["months"] = months

            all_dates = set()

            # 逐月读取当前系统可查询的日期
            for month in months:
                if month < today.strftime("%Y%m"):
                    continue

                switch_month(page, month)

                dates = get_dates(page)

                log(f"{month} 读取到日期数量：{len(dates)}")

                for date_text in dates:
                    try:
                        date_value = datetime.strptime(
                            date_text,
                            "%Y-%m-%d"
                        ).date()

                        if date_value >= today:
                            all_dates.add(date_text)

                    except ValueError:
                        continue

            dates_to_check = sorted(all_dates)

            report["date_count"] = len(dates_to_check)

            log(f"总共需要查询 {len(dates_to_check)} 天")

            if not dates_to_check:
                save_debug_page(page, "no_dates")
                raise RuntimeError("没有读取到今天及以后的可查询日期")

            all_available = []

            # 逐日检查空位
            for date_text in dates_to_check:
                try:
                    month = date_text[:4] + date_text[5:7]

                    current_month = page.locator(
                        'select[name="Ym_select"]'
                    ).input_value()

                    if current_month != month:
                        switch_month(page, month)

                    open_day(page, date_text)

                    daily = read_daily_availability(
                        page,
                        date_text
                    )

                    all_available.extend(daily)

                    log(
                        f"{date_text} 查询完成，"
                        f"发现 {len(daily)} 个空位"
                    )

                except Exception as e:
                    error_text = f"{date_text}: {str(e)}"
                    report["errors"].append(error_text)
                    log(f"当天查询失败：{error_text}")

            report["available"] = all_available
            report["available_count"] = len(all_available)

            log(f"查询结束，共发现 {len(all_available)} 个可预约时段")

            # 只有完整查询成功时才更新状态
            # 避免部分日期查询失败时错误覆盖旧状态
            if report["errors"]:
                log("部分日期查询失败，本次暂不更新状态文件。")
            else:
                old_state = load_state()

                current_state = {
                    item_key(item)
                    for item in all_available
                }

                new_items = [
                    item for item in all_available
                    if item_key(item) not in old_state
                ]

                if not STATE_FILE.exists():
                    log("首次运行：建立空位基准并发送当前空位通知。")
                    send_ntfy(all_available)
                else:
                    log(f"新增空位数量：{len(new_items)}")
                    send_ntfy(new_items)

                save_state(all_available)

            save_report(report)

            log("报告已保存：artifacts/report.json")

    except Exception as e:
        report["fatal_error"] = str(e)
        report["errors"].append(str(e))

        log(f"运行失败：{e}")

        save_report(report)

        raise

    finally:
        # 先关闭浏览器，再退出 Playwright 上下文
        if browser is not None:
            try:
                browser.close()
            except Exception as e:
                log(f"关闭浏览器时出现非致命异常：{e}")

        if context is not None:
            try:
                context.close()
            except Exception as e:
                log(f"关闭浏览器上下文时出现非致命异常：{e}")


if __name__ == "__main__":
    main()
