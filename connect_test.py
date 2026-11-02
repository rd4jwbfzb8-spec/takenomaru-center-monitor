
import json
import re
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# =========================
# 设置
# =========================

OFFICIAL_URL = "https://www.nakakumin.com/takenomaruchikusen/"
NTFY_TOPIC = "minami-rsv-k8p4x7q2m9"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

STATE_FILE = Path("state.json")
REPORT_DIR = Path("artifacts")
REPORT_FILE = REPORT_DIR / "report.json"

JST = ZoneInfo("Asia/Tokyo")

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


def save_debug(page, name):
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
    except Exception as e:
        log(f"诊断文件保存失败：{e}")


def wait_page(page):
    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=10000
        )
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(2000)


# =========================
# 官网入口
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

    # 不等待所有资源加载完成，避免官网图片等资源拖慢加载
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
                    "官网连续三次无法打开，请检查官网或 GitHub 网络连接。"
                ) from e

            page.wait_for_timeout(3000)

    if response is not None:
        log(f"官网 HTTP 状态：{response.status}")

    log("2. 找到官网预约入口")

    # 使用官网真实表单，保留原来的 POST 参数
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
            "官网没有找到之前确认成功的预约 POST 表单。"
        )

    log("已确认官网预约表单及 POST 参数")

    log("3. 提交官网预约表单")

    # 官网表单原本 target=_blank，保留新窗口行为
    popup = None

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
                "提交官网表单后没有打开预约系统窗口。"
            )

    wait_page(popup)

    log(f"预约系统 URL：{popup.url}")
    log(f"预约系统标题：{popup.title()}")

    # 等待预约系统真正出现
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
        raise RuntimeError("没有找到空き状況からみる按钮")

    authority.click()
    wait_page(popup)

    log("5. 选择竹之丸地区中心体育室")

    grand = popup.locator('select[name="grand"]')
    grand.wait_for(state="visible")

    grand.select_option("4")

    # 使用预约页面原有表单提交
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
# 月份读取
# =========================

def get_months(page):
    log("6. 读取可查询月份")

    options = page.locator(
        'select[name="Ym_select"] option'
    ).evaluate_all(
        """elements => elements.map(e => ({
            value: e.value,
            text: e.textContent.trim()
        }))"""
    )

    months = sorted({
        str(item["value"])
        for item in options
        if re.fullmatch(r"\\d{6}", str(item["value"]))
    })

    if not months:
        save_debug(page, "months_error")
        raise RuntimeError("没有读取到可查询月份")

    log(f"读取到月份：{months}")

    return months


def switch_month(page, month):
    has_function = page.evaluate(
        "() => typeof SubmitFormM === 'function'"
    )

    if has_function:
        page.evaluate(
            "(m) => SubmitFormM(m)",
            month
        )
    else:
        select = page.locator('select[name="Ym_select"]')
        select.select_option(month)
        select.evaluate(
            """element => element.form.requestSubmit()"""
        )

    wait_page(page)


def get_dates(page):
    onclicks = page.locator(
        '[onclick*="SubmitFormD("]'
    ).evaluate_all(
        """elements => elements.map(e => e.getAttribute("onclick"))"""
    )

    dates = set()

    for onclick in onclicks:
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
# 日期查询
# =========================

def open_day(page, date_text):
    page.evaluate(
        "(d) => SubmitFormD(d)",
        date_text
    )

    page.wait_for_function(
        """date => {
            const tables = document.querySelectorAll("table.table_base");

            return Array.from(tables).some(table => {
                const text = table.innerText || "";
                return text.includes(date);
            });
        }""",
        date_text,
        timeout=20000
    )

    page.wait_for_timeout(500)


# =========================
# 空位判断
# =========================

def cell_is_available(cell):
    checkboxes = cell.locator(
        'input[type="checkbox"]'
    )

    for i in range(checkboxes.count()):
        if checkboxes.nth(i).is_enabled():
            return True

    return False


def read_daily(page, date_text):
    table = page.locator("table.table_base").last

    if table.count() == 0:
        raise RuntimeError("没有找到当天预约表格")

    rows = table.locator("tr")
    available = []

    for i in range(rows.count()):
        cells = rows.nth(i).locator("td")

        if cells.count() < 5:
            continue

        room_name = re.sub(
            r"\\s+",
            "",
            cells.nth(0).inner_text()
        )

        room_code = None

        for name, code in ROOMS.items():
            if name in room_name:
                room_code = code
                break

        if room_code is None:
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
# 通知和状态
# =========================

def item_key(item):
    return json.dumps(
        item,
        ensure_ascii=False,
        sort_keys=True
    )


def load_state():
    if not STATE_FILE.exists():
        return None

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
        return {item_key(item) for item in data}
    except Exception:
        return None


def save_state(items):
    STATE_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def send_ntfy(items):
    if not items:
        return

    lines = ["竹之丸地区中心 体育室发现空位"]

    for item in items:
        lines.append(
            f"{item['date']} 体育室{item['room']} {item['time']}"
        )

    request = Request(
        NTFY_URL,
        data="\\n".join(lines).encode("utf-8"),
        headers={
            "Title": "竹之丸地区中心空位通知",
            "Priority": "high"
        },
        method="POST"
    )

    with urlopen(request, timeout=20) as response:
        log(f"ntfy 通知结果：HTTP {response.status}")


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

    with sync_playwright() as p:
        browser = None
        context = None

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

                        if date_value >= today:
                            dates.add(date_text)

                    except ValueError:
                        pass

            dates = sorted(dates)
            report["date_count"] = len(dates)

            log(f"待查询日期总数：{len(dates)}")

            if not dates:
                raise RuntimeError("没有找到今天及以后的日期")

            all_available = []

            for date_text in dates:
                try:
                    month = date_text[:4] + date_text[5:7]

                    current_month = page.locator(
                        'select[name="Ym_select"]'
                    ).input_value()

                    if current_month != month:
                        switch_month(page, month)

                    open_day(page, date_text)

                    daily = read_daily(page, date_text)

                    all_available.extend(daily)

                    log(
                        f"{date_text} 查询完成，"
                        f"空位 {len(daily)} 个"
                    )

                except Exception as e:
                    message = f"{date_text}: {e}"
                    report["errors"].append(message)
                    log(f"日期查询失败：{message}")

            report["available"] = all_available
            report["available_count"] = len(all_available)

            if report["errors"]:
                log("存在日期查询失败，本次不更新状态。")
            else:
                old_state = load_state()

                if old_state is None:
                    log("首次运行，建立空位基准并通知当前空位")
                    send_ntfy(all_available)
                else:
                    new_items = [
                        item for item in all_available
                        if item_key(item) not in old_state
                    ]

                    log(f"新增空位：{len(new_items)}")
                    send_ntfy(new_items)

                save_state(all_available)

            save_report(report)

            log("查询完成，报告已保存")

        except Exception as e:
            report["fatal_error"] = str(e)
            report["errors"].append(str(e))

            save_report(report)

            log(f"运行失败：{e}")
            raise

        finally:
            # 必须在 Playwright 上下文退出前关闭浏览器
            if browser is not None:
                try:
                    browser.close()
                except Exception as e:
                    log(f"浏览器关闭异常：{e}")


if __name__ == "__main__":
    main()
