
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


OFFICIAL_URL = "https://www.nakakumin.com/takenomaruchikusen/"
NTFY_TOPIC = "minami-rsv-k8p4x7q2m9"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

STATE_FILE = "state.json"
REPORT_FILE = "artifacts/report.json"

ROOMS = {
    "体育室（Ａ）": "A",
    "体育室（Ｂ）": "B",
    "体育室（Ｃ）": "C",
}


def wait_for_submit(page, action):
    """执行表单提交，并等待页面更新。"""
    try:
        with page.expect_navigation(
            wait_until="domcontentloaded",
            timeout=20000
        ):
            action()
    except PlaywrightTimeoutError:
        # 某些页面可能通过局部更新完成操作
        pass

    page.wait_for_timeout(1000)


def normalize_text(value):
    return re.sub(r"\s+", "", value or "")


def get_calendar_dates(page):
    """读取当前月份页面中所有可点击日期。"""
    return page.locator(
        'input[onclick^="SubmitFormD("]'
    ).evaluate_all(
        """els => els.map(el => {
            const match = el.getAttribute("onclick")
                .match(/SubmitFormD\\('([^']+)'\\)/);
            return match ? match[1] : null;
        }).filter(Boolean)"""
    )


def get_available_in_cell(cell):
    """
    只识别真正可以选择的空位。
    已禁用的复选框不算可预约。
    """
    checkbox = cell.locator('input[type="checkbox"]')

    if checkbox.count() > 0:
        return not checkbox.first.is_disabled()

    # 兼容页面使用空位图标的情况
    images = cell.locator("img")

    for i in range(images.count()):
        src = images.nth(i).get_attribute("src") or ""

        if "msg_icon05" in src:
            return True

    cell_class = cell.get_attribute("class") or ""

    if "koma_05" in cell_class:
        return True

    return False


def read_daily_availability(page, target_date):
    """读取某一天 A/B/C 三个场地的四个时段。"""

    expected_caption = (
        f"{target_date.year}年"
        f"{target_date.month:02d}月"
        f"{target_date.day:02d}日"
    )

    page.wait_for_function(
        """target => {
            const caption = document.querySelector(
                "table.table_base caption"
            );
            return caption && caption.innerText.includes(target);
        }""",
        arg=expected_caption,
        timeout=20000
    )

    table = page.locator("table.table_base").last
    rows = table.locator("tr")

    if rows.count() < 2:
        raise RuntimeError(f"{target_date} 没有读取到预约表格")

    # 自动读取页面实际显示的时间段
    header_cells = rows.nth(0).locator("td")
    times = []

    for i in range(1, min(header_cells.count(), 5)):
        times.append(
            normalize_text(header_cells.nth(i).inner_text())
        )

    if not times:
        raise RuntimeError(f"{target_date} 没有读取到时间段")

    available = []

    for row_index in range(1, rows.count()):
        row = rows.nth(row_index)
        cells = row.locator("td")

        if cells.count() < 2:
            continue

        room_name = normalize_text(cells.nth(0).inner_text())

        if room_name not in ROOMS:
            continue

        room = ROOMS[room_name]

        for time_index in range(1, min(cells.count(), len(times) + 1)):
            cell = cells.nth(time_index)

            if get_available_in_cell(cell):
                available.append({
                    "date": target_date.isoformat(),
                    "room": room,
                    "time": times[time_index - 1]
                })

    return available


def load_previous_state():
    if not os.path.exists(STATE_FILE):
        return set()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return set(data.get("available_keys", []))

    except Exception:
        return set()


def make_key(slot):
    return (
        f"{slot['date']}|"
        f"{slot['room']}|"
        f"{slot['time']}"
    )


def send_ntfy(new_slots):
    if not new_slots:
        return

    lines = [
        "竹之丸地区センター 体育室发现新空位！",
        "",
    ]

    for slot in new_slots:
        lines.append(
            f"{slot['date']}  "
            f"{slot['room']}场地  "
            f"{slot['time']}"
        )

    message = "\n".join(lines)

    request = urllib.request.Request(
        NTFY_URL,
        data=message.encode("utf-8"),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Priority": "default",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(
                f"ntfy 通知失败，HTTP {response.status}"
            )


def save_state(available_slots):
    keys = sorted(make_key(slot) for slot in available_slots)

    data = {
        "updated_at": datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).isoformat(),
        "available_keys": keys,
    }

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    os.makedirs("artifacts", exist_ok=True)

    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()

    report = {
        "test_time": datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).isoformat(),
        "monitor": "竹之丸地区センター（体育室）",
        "rooms": ["A", "B", "C"],
        "date_range": "预约系统当前可查询的全部未来日期",
        "checked_dates": [],
        "available_slots": [],
        "new_slots": [],
        "errors": [],
        "scan_complete": False,
    }

    browser = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()

            page.goto(
                OFFICIAL_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            # 通过官网表单打开预约系统
            with context.expect_page(timeout=30000) as popup_info:
                page.locator(
                    'form[action*="VisitorLogin.php"] '
                    'input[type="submit"]'
                ).first.click()

            reservation_page = popup_info.value
            reservation_page.wait_for_load_state(
                "domcontentloaded"
            )

            # 选择「空き状況からみる」
            wait_for_submit(
                reservation_page,
                lambda: reservation_page.locator(
                    'input[name="authority"]'
                    '[value="空き状況からみる"]'
                ).click()
            )

            # 选择竹之丸体育室 grand=4
            grand_select = reservation_page.locator(
                'select[name="grand"]'
            )

            grand_select.select_option("4")

            wait_for_submit(
                reservation_page,
                lambda: grand_select.evaluate(
                    "(el) => el.form.submit()"
                )
            )

            if grand_select.input_value() != "4":
                raise RuntimeError("切换竹之丸体育室失败")

            # 获取预约系统提供的全部月份
            month_options = reservation_page.locator(
                'select[name="Ym_select"] option'
            ).evaluate_all(
                """els => els.map(el => ({
                    value: el.value,
                    text: el.textContent.trim()
                }))"""
            )

            months = sorted({
                item["value"]
                for item in month_options
                if re.fullmatch(r"\\d{6}", item["value"])
            })

            if not months:
                raise RuntimeError("没有读取到可查询月份")

            report["queryable_months"] = months

            # 逐月读取日历，收集全部可查询日期
            all_dates = set()

            for month in months:
                current_month = reservation_page.locator(
                    'select[name="Ym_select"]'
                ).input_value()

                if current_month != month:
                    month_select = reservation_page.locator(
                        'select[name="Ym_select"]'
                    )

                    month_select.select_option(month)

                    wait_for_submit(
                        reservation_page,
                        lambda: month_select.evaluate(
                            "(el) => el.form.submit()"
                        )
                    )

                for date_text in get_calendar_dates(
                    reservation_page
                ):
                    try:
                        parsed_date = date.fromisoformat(date_text)

                        if parsed_date >= today:
                            all_dates.add(date_text)

                    except ValueError:
                        continue

            dates_to_check = sorted(all_dates)

            if not dates_to_check:
                raise RuntimeError("没有找到可检查的未来日期")

            report["date_count"] = len(dates_to_check)

            all_available = []

            # 逐日读取 A/B/C
            for date_text in dates_to_check:
                target_date = date.fromisoformat(date_text)
                month = target_date.strftime("%Y%m")

                current_month = reservation_page.locator(
                    'select[name="Ym_select"]'
                ).input_value()

                if current_month != month:
                    month_select = reservation_page.locator(
                        'select[name="Ym_select"]'
                    )

                    month_select.select_option(month)

                    wait_for_submit(
                        reservation_page,
                        lambda: month_select.evaluate(
                            "(el) => el.form.submit()"
                        )
                    )

                wait_for_submit(
                    reservation_page,
                    lambda: reservation_page.evaluate(
                        "(d) => SubmitFormD(d)",
                        date_text
                    )
                )

                daily_slots = read_daily_availability(
                    reservation_page,
                    target_date
                )

                all_available.extend(daily_slots)
                report["checked_dates"].append(date_text)

                print(
                    f"已检查 {date_text}，"
                    f"发现 {len(daily_slots)} 个空位"
                )

            report["available_slots"] = all_available
            report["scan_complete"] = True

            # 与上次结果比较，只通知新出现的空位
            previous_keys = load_previous_state()
            current_keys = {
                make_key(slot) for slot in all_available
            }

            new_slots = [
                slot for slot in all_available
                if make_key(slot) not in previous_keys
            ]

            report["new_slots"] = new_slots

            # 先发送通知，成功后再保存状态
            if new_slots:
                send_ntfy(new_slots)

            save_state(all_available)

            print("检查完成。")
            print(f"查询月份：{months}")
            print(f"检查日期：{len(dates_to_check)} 天")
            print(f"当前空位：{len(all_available)} 个")
            print(f"新空位：{len(new_slots)} 个")

    except Exception as e:
        report["errors"].append(str(e))
        print(f"运行失败：{e}")

    finally:
        if browser:
            browser.close()

        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(
                report,
                f,
                ensure_ascii=False,
                indent=2
            )

        print("========== TAKENOMARU MONITOR REPORT ==========")
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if not report["scan_complete"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
