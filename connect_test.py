
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ============================================================
# 竹之丸地区センター 体育室空位监控
# ============================================================

OFFICIAL_URL = "https://www.nakakumin.com/takenomaruchikusen/"
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "minami-rsv-k8p4x7q2m9")
NTFY_URL = "https://ntfy.sh"
STATE_FILE = Path("state.json")
REPORT_DIR = Path("artifacts")
REPORT_FILE = REPORT_DIR / "monitor_report.json"
JST = ZoneInfo("Asia/Tokyo")

# 只监控 A/B/C 单独区域；组合区域和全面不参与判断。
ROOMS = {
    "体育室（Ａ）": "A",
    "体育室（Ｂ）": "B",
    "体育室（Ｃ）": "C",
}

# 每次运行最多查询的日期数。
MAX_DATES_PER_RUN = 120


def log(message):
    print(
        f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {message}",
        flush=True,
    )


def save_report(report):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def save_debug(page, name):
    """必须在 Playwright 仍然运行时调用。"""
    if page is None:
        log("没有可用页面，跳过错误现场保存")
        return

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if page.is_closed():
            log("页面已经关闭，无法保存 HTML")
        else:
            (REPORT_DIR / f"{name}.html").write_text(
                page.content(),
                encoding="utf-8",
            )
            log(f"已保存错误现场 HTML：artifacts/{name}.html")
    except Exception as exc:
        log(f"保存错误现场 HTML 失败：{exc}")

    try:
        if page.is_closed():
            log("页面已经关闭，无法保存截图")
        else:
            page.screenshot(
                path=str(REPORT_DIR / f"{name}.png"),
                full_page=True,
                timeout=20000,
            )
            log(f"已保存错误现场截图：artifacts/{name}.png")
    except Exception as exc:
        log(f"保存错误现场截图失败：{exc}")


def wait_page(page, milliseconds=1200):
    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=12000,
        )
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(milliseconds)


# ============================================================
# 官网入口：沿用已经验证成功的流程
# ============================================================

def enter_reservation(browser):
    context = browser.new_context(
        locale="ja-JP",
        viewport={"width": 1440, "height": 1000},
    )

    page = context.new_page()
    page.set_default_timeout(20000)
    page.set_default_navigation_timeout(90000)

    log("1. 打开竹之丸地区中心官网")

    for attempt in range(1, 4):
        try:
            log(f"官网连接尝试：{attempt}/3")

            response = page.goto(
                OFFICIAL_URL,
                wait_until="commit",
                timeout=45000,
            )

            wait_page(page)

            log(f"官网页面：{page.url}")
            log(f"官网标题：{page.title()}")

            if response is not None:
                log(f"官网 HTTP 状态：{response.status}")

            break

        except Exception as exc:
            log(f"官网连接失败：{exc}")

            if attempt == 3:
                save_debug(page, "official_error")
                raise RuntimeError(
                    "官网连续三次无法打开"
                ) from exc

            page.wait_for_timeout(3000)

    log("2. 查找官网预约 POST 表单")

    entry_form = None
    forms = page.locator("form")

    for index in range(forms.count()):
        form = forms.nth(index)
        action = form.get_attribute("action") or ""

        values = {}
        inputs = form.locator("input")

        for input_index in range(inputs.count()):
            item = inputs.nth(input_index)
            name = item.get_attribute("name")

            if name:
                values[name] = item.get_attribute("value")

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

    log("3. 提交官网预约表单并打开预约系统窗口")

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
        timeout=30000,
    )

    log("4. 选择「空き状況からみる」")

    authority = popup.locator(
        'input[name="authority"][value="空き状況からみる"]'
    )

    if authority.count() == 0:
        save_debug(popup, "authority_error")
        raise RuntimeError(
            "没有找到「空き状況からみる」按钮"
        )

    authority.click()
    wait_page(popup)

    log("5. 选择竹之丸地区中心体育室（grand=4）")

    grand = popup.locator('select[name="grand"]')
    grand.wait_for(state="visible", timeout=30000)
    grand.select_option("4")

    # 使用 select 所属的表单提交，避免提交错误表单。
    grand.evaluate(
        """element => {
            if (!element.form) {
                throw new Error('设施选择表单不存在');
            }
            element.form.requestSubmit();
        }"""
    )

    popup.wait_for_selector(
        'select[name="Ym_select"]',
        timeout=45000,
    )

    wait_page(popup, 500)

    log(f"体育室预约页面：{popup.url}")
    log(f"体育室页面标题：{popup.title()}")

    return context, popup


# ============================================================
# 读取可查询月份和日期
# ============================================================

def get_months(page):
    values = page.locator(
        'select[name="Ym_select"] option'
    ).evaluate_all(
        "elements => elements.map(element => element.value)"
    )

    months = sorted({
        str(value)
        for value in values
        if re.fullmatch(r"\d{6}", str(value))
    })

    if not months:
        save_debug(page, "months_error")
        raise RuntimeError("没有读取到可查询月份")

    log(f"网页可查询月份：{months}")

    return months


def current_month(page):
    return page.locator(
        'select[name="Ym_select"]'
    ).input_value()


def switch_month(page, month):
    if current_month(page) == month:
        return

    log(f"切换到月份：{month}")

    has_function = page.evaluate(
        "() => typeof SubmitFormM === 'function'"
    )

    if has_function:
        page.evaluate(
            "month => SubmitFormM(month)",
            month,
        )
    else:
        selector = page.locator(
            'select[name="Ym_select"]'
        )
        selector.select_option(month)
        selector.evaluate(
            "element => element.form.requestSubmit()"
        )

    page.wait_for_function(
        """month => {
            const el = document.querySelector(
                'select[name="Ym_select"]'
            );
            return !!el && el.value === month;
        }""",
        arg=month,
        timeout=45000,
    )

    page.wait_for_selector(
        "table.table_base",
        timeout=30000,
    )

    log(f"月份切换完成：{current_month(page)}")


def get_dates(page):
    onclicks = page.locator(
        '[onclick*="SubmitFormD("]'
    ).evaluate_all(
        "elements => elements.map(element => element.getAttribute('onclick'))"
    )

    dates = set()

    for onclick in onclicks:
        if not onclick:
            continue

        match = re.search(
            r"SubmitFormD\(['\"](\d{4}-\d{2}-\d{2})['\"]\)",
            onclick,
        )

        if match:
            dates.add(match.group(1))

    return sorted(dates)


def japanese_date(date_text):
    value = datetime.strptime(
        date_text,
        "%Y-%m-%d",
    ).date()

    return (
        f"{value.year}年"
        f"{value.month:02d}月"
        f"{value.day:02d}日"
    )


# ============================================================
# 日期切换：最多重试三次
# ============================================================

def switch_date(page, date_text):
    expected = japanese_date(date_text)

    def already_on_target():
        try:
            caption = page.locator(
                "table.table_base caption"
            )

            return (
                caption.count() > 0
                and expected in caption.first.inner_text(
                    timeout=3000
                )
            )

        except Exception:
            return False

    # 页面已经是目标日期，不重复切换。
    if already_on_target():
        log(f"{date_text} 页面已经是目标日期，无需重复切换")
        return

    log(f"查询日期：{date_text}")

    last_error = None

    for attempt in range(1, 4):
        log(
            f"日期切换尝试：{attempt}/3，"
            f"目标：{date_text}"
        )

        try:
            # 每次尝试前先检查一次，防止重复切换。
            if already_on_target():
                log(f"{date_text} 日期已切换成功")
                return

            has_function = page.evaluate(
                "() => typeof SubmitFormD === 'function'"
            )

            if not has_function:
                raise RuntimeError(
                    "页面不存在 SubmitFormD 函数"
                )

            # 调用预约系统原有日期切换函数。
            try:
                with page.expect_navigation(
                    wait_until="domcontentloaded",
                    timeout=20000,
                ):
                    page.evaluate(
                        "dateText => SubmitFormD(dateText)",
                        date_text,
                    )

            except PlaywrightTimeoutError:
                log(
                    "未检测到标准导航事件，"
                    "继续检查预约表标题"
                )

            # 如果页面发生导航，等待 DOM 恢复。
            try:
                page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=10000,
                )
            except PlaywrightTimeoutError:
                pass

            # 不再等待 45 秒，改为 15 秒检查一次。
            page.wait_for_function(
                """expected => {
                    const table = document.querySelector(
                        'table.table_base caption'
                    );

                    return !!table &&
                        table.innerText.includes(expected);
                }""",
                arg=expected,
                timeout=15000,
            )

            actual_caption = page.locator(
                "table.table_base caption"
            ).inner_text()

            if expected not in actual_caption:
                raise RuntimeError(
                    f"日期标题不匹配："
                    f"目标={expected}，"
                    f"实际={actual_caption}"
                )

            log(f"{date_text} 日期切换成功")
            return

        except Exception as exc:
            last_error = exc

            log(
                f"{date_text} 第 {attempt} 次切换失败："
                f"{type(exc).__name__}: {exc}"
            )

            # 页面可能已经切换成功，只是等待过程出错。
            if already_on_target():
                log(f"{date_text} 实际已经切换成功")
                return

            if attempt < 3:
                log("等待 3 秒后重试")
                page.wait_for_timeout(3000)

    # 三次都失败，在浏览器关闭前保存错误现场。
    save_debug(
        page,
        f"date_error_{date_text.replace('-', '')}",
    )

    try:
        actual = page.locator(
            "table.table_base caption"
        ).first.inner_text(timeout=3000)
    except Exception:
        actual = "无法读取预约表标题"

    raise RuntimeError(
        f"日期 {date_text} 连续 3 次切换失败；"
        f"当前页面标题={actual}；"
        f"最后错误={last_error}"
    )


# ============================================================
# 解析单日表格
#
# 只看 A/B/C 的 checkbox 是否存在。
# 不区分 disabled。
# 不看图标、横杠、组合场地。
# ============================================================

def normalize_slot(text):
    return re.sub(
        r"\s+",
        "",
        text or "",
    ).replace("〜", "～")


def parse_day(page, date_text):
    table = page.locator("table.table_base")

    if table.count() != 1:
        raise RuntimeError(
            f"{date_text}：预约表数量异常，"
            f"找到 {table.count()} 张"
        )

    caption = table.locator("caption").inner_text()
    expected = japanese_date(date_text)

    if expected not in caption:
        raise RuntimeError(
            f"{date_text}：预约表标题不匹配：{caption}"
        )

    rows = table.locator("tr")

    if rows.count() < 4:
        raise RuntimeError(
            f"{date_text}：预约表行数异常：{rows.count()}"
        )

    header_cells = rows.nth(0).locator("td")

    if header_cells.count() < 2:
        raise RuntimeError(
            f"{date_text}：没有读取到时段标题"
        )

    slots = [
        normalize_slot(
            header_cells.nth(i).inner_text()
        )
        for i in range(1, header_cells.count())
    ]

    if (
        len(slots) not in (3, 4)
        or any(not slot for slot in slots)
    ):
        raise RuntimeError(
            f"{date_text}：时段数量或标题异常：{slots}"
        )

    found_rooms = set()
    result = {}

    for row_index in range(rows.count()):
        row = rows.nth(row_index)
        cells = row.locator("td")

        if cells.count() < 2:
            continue

        room_label = cells.nth(0).inner_text().strip()

        if room_label not in ROOMS:
            continue

        room_code = ROOMS[room_label]

        if room_code in found_rooms:
            raise RuntimeError(
                f"{date_text}：区域 {room_code} 出现重复数据行"
            )

        found_rooms.add(room_code)

        expanded_cells = []

        for cell_index in range(1, cells.count()):
            cell = cells.nth(cell_index)

            try:
                span = int(
                    cell.get_attribute("colspan") or "1"
                )
            except ValueError:
                span = 1

            if span < 1:
                span = 1

            expanded_cells.extend([cell] * span)

        if len(expanded_cells) != len(slots):
            raise RuntimeError(
                f"{date_text}：区域 {room_code} 的格数与时段数不一致，"
                f"格数={len(expanded_cells)}，"
                f"时段数={len(slots)}"
            )

        for slot, cell in zip(slots, expanded_cells):
            # 用户确认的规则：
            # checkbox 存在即可，disabled 也算空位。
            has_checkbox = (
                cell.locator(
                    'input[type="checkbox"]'
                ).count() > 0
            )

            key = f"{date_text}|{room_code}|{slot}"

            result[key] = {
                "date": date_text,
                "room": room_code,
                "slot": slot,
                "available": has_checkbox,
            }

    if found_rooms != set(ROOMS.values()):
        missing = sorted(
            set(ROOMS.values()) - found_rooms
        )

        raise RuntimeError(
            f"{date_text}：没有完整读取 A/B/C 三行，"
            f"缺少：{missing}"
        )

    expected_cells = len(ROOMS) * len(slots)

    if len(result) != expected_cells:
        raise RuntimeError(
            f"{date_text}：读取单元格数量异常："
            f"{len(result)} / {expected_cells}"
        )

    available_count = sum(
        1
        for item in result.values()
        if item["available"]
    )

    log(
        f"{date_text} 读取完成："
        f"A/B/C 共 {expected_cells} 格，"
        f"发现 checkbox 空位 {available_count} 格"
    )

    return result


# ============================================================
# state.json 与 ntfy
# ============================================================

def load_state():
    if not STATE_FILE.exists():
        return None

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "state.json 存在但无法读取，拒绝覆盖旧状态"
        ) from exc

    # 兼容带 cells 的新格式，以及旧版直接以 key 为顶层的格式。
    if (
        isinstance(data, dict)
        and isinstance(data.get("cells"), dict)
    ):
        return data["cells"]

    if isinstance(data, dict):
        return data

    raise RuntimeError(
        "state.json 格式异常，拒绝覆盖旧状态"
    )


def was_available(value):
    if isinstance(value, dict):
        return value.get("available") is True

    return value is True


def send_ntfy(items, first_run=False):
    if not NTFY_TOPIC:
        raise RuntimeError(
            "NTFY_TOPIC 为空，无法发送通知"
        )

    items = sorted(
        items,
        key=lambda item: (
            item["date"],
            item["room"],
            item["slot"],
        ),
    )

    heading = (
        "竹之丸地区センター：当前检测到以下 checkbox 空位（首次基准）"
        if first_run
        else "竹之丸地区センター：发现新的体育室空位"
    )

    # 分批发送，避免单条消息过长。
    chunks = []
    current_lines = [heading, ""]

    for item in items:
        line = (
            f'{item["date"]}  '
            f'体育室 {item["room"]}  '
            f'{item["slot"]}'
        )

        if (
            len("\n".join(current_lines + [line])) > 3000
            and len(current_lines) > 2
        ):
            chunks.append("\n".join(current_lines))
            current_lines = [heading, ""]

        current_lines.append(line)

    if len(current_lines) > 2:
        chunks.append("\n".join(current_lines))

    total = len(chunks)

    for index, message in enumerate(chunks, start=1):
        if total > 1:
            message += f"\n\n（第 {index}/{total} 条）"

        response = requests.post(
            NTFY_URL,
            json={
                "topic": NTFY_TOPIC,
                "title": "竹之丸地区中心空位通知",
                "message": message,
                "priority": 4,
                "tags": ["badminton", "calendar"],
            },
            timeout=30,
        )

        response.raise_for_status()

    log(
        f"ntfy 通知发送完成："
        f"{len(items)} 个空位，{total} 条消息"
    )


def save_state(cells):
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(JST).isoformat(),
        "cells": cells,
    }

    fd, temp_name = tempfile.mkstemp(
        prefix="state_",
        suffix=".json",
        dir=str(STATE_FILE.parent),
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")

        os.replace(
            temp_name,
            STATE_FILE,
        )

    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


# ============================================================
# 主程序
# ============================================================

def main():
    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    today = datetime.now(JST).date()

    report = {
        "run_time": datetime.now(JST).isoformat(),
        "today": today.isoformat(),
        "purpose": "竹之丸体育室 A/B/C checkbox 空位监控",
        "months": [],
        "dates_found": 0,
        "dates_checked": 0,
        "cells_checked": 0,
        "available_count": 0,
        "newly_available_count": 0,
        "first_run": False,
        "errors": [],
    }

    try:
        # 所有 Playwright 页面操作及错误现场保存，
        # 都放在 sync_playwright 生命周期内部。
        with sync_playwright() as playwright:

            browser = None
            context = None
            page = None

            try:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox"],
                )

                context, page = enter_reservation(browser)

                # ------------------------------------------------
                # 读取所有可查询月份
                # ------------------------------------------------

                months = get_months(page)
                report["months"] = months

                current_ym = today.strftime("%Y%m")
                all_dates = set()

                for month in months:
                    if month < current_ym:
                        continue

                    switch_month(page, month)

                    month_dates = get_dates(page)

                    log(
                        f"{month} 日历中找到 "
                        f"{len(month_dates)} 个日期"
                    )

                    for date_text in month_dates:
                        try:
                            parsed_date = datetime.strptime(
                                date_text,
                                "%Y-%m-%d",
                            ).date()

                        except ValueError:
                            continue

                        if parsed_date >= today:
                            all_dates.add(date_text)

                dates = sorted(all_dates)

                report["dates_found"] = len(dates)

                log(
                    f"今天及未来可查询日期："
                    f"{len(dates)} 天"
                )

                if not dates:
                    raise RuntimeError(
                        "没有找到今天及以后的可查询日期"
                    )

                if len(dates) > MAX_DATES_PER_RUN:
                    raise RuntimeError(
                        f"可查询日期数量异常：{len(dates)}，"
                        f"超过安全上限 {MAX_DATES_PER_RUN}"
                    )

                # ------------------------------------------------
                # 逐日查询预约表
                # ------------------------------------------------

                current = {}
                active_month = current_month(page)

                for date_text in dates:
                    target_month = (
                        date_text[:4] + date_text[5:7]
                    )

                    if active_month != target_month:
                        switch_month(
                            page,
                            target_month,
                        )
                        active_month = target_month

                    switch_date(
                        page,
                        date_text,
                    )

                    day_cells = parse_day(
                        page,
                        date_text,
                    )

                    overlap = set(current).intersection(
                        day_cells
                    )

                    if overlap:
                        raise RuntimeError(
                            "发现重复日期/单元格记录："
                            f"{sorted(overlap)[:3]}"
                        )

                    current.update(day_cells)

                    report["dates_checked"] += 1

                # ------------------------------------------------
                # 完整性检查
                # ------------------------------------------------

                if (
                    not current
                    or report["dates_checked"] != len(dates)
                ):
                    raise RuntimeError(
                        "查询结果不完整，"
                        "拒绝发送通知或更新 state.json"
                    )

                report["cells_checked"] = len(current)

                report["available_count"] = sum(
                    1
                    for item in current.values()
                    if item["available"]
                )

                log(
                    f"全部日期查询完成："
                    f"{report['dates_checked']} 天，"
                    f"{report['cells_checked']} 个单元格，"
                    f"checkbox 空位 "
                    f"{report['available_count']} 个"
                )

                # ------------------------------------------------
                # 所有日期全部成功后，才读取旧状态
                # ------------------------------------------------

                previous = load_state()

                if previous is None:
                    report["first_run"] = True

                    to_notify = [
                        item
                        for item in current.values()
                        if item["available"]
                    ]

                    if to_notify:
                        send_ntfy(
                            to_notify,
                            first_run=True,
                        )
                    else:
                        log(
                            "首次运行没有发现 checkbox 空位，"
                            "不发送通知"
                        )

                else:
                    to_notify = []

                    for key, item in current.items():
                        old_value = previous.get(key)

                        # 仅比较上次已监控过的格子。
                        # 新出现的日期不作为空位变化通知。
                        if (
                            old_value is not None
                            and not was_available(old_value)
                            and item["available"]
                        ):
                            to_notify.append(item)

                    report["newly_available_count"] = len(
                        to_notify
                    )

                    if to_notify:
                        send_ntfy(
                            to_notify,
                            first_run=False,
                        )
                    else:
                        log(
                            "没有发现从无 checkbox "
                            "变为有 checkbox 的新空位"
                        )

                # 只有查询完整且通知成功后才更新状态。
                save_state(current)

                log("state.json 已成功更新")

                report["status"] = "success"

                save_report(report)

                if report["first_run"]:
                    log("首次基准建立完成")
                else:
                    log("本次监控运行完成")

            except Exception as exc:
                # 关键修复：
                # 在浏览器和 Playwright 仍然运行时保存错误现场。
                report["status"] = "failed"
                report["errors"].append(str(exc))

                log(f"运行失败：{exc}")

                if page is not None:
                    save_debug(
                        page,
                        "monitor_error",
                    )

                save_report(report)

                raise

            finally:
                # 先关闭页面上下文和浏览器，
                # 再退出 sync_playwright。
                if context is not None:
                    try:
                        context.close()
                    except Exception as exc:
                        log(f"关闭浏览器上下文失败：{exc}")

                if browser is not None:
                    try:
                        browser.close()
                    except Exception as exc:
                        log(f"关闭浏览器失败：{exc}")

    except Exception as error:
        # 外层只负责确保报告存在，不再访问已经关闭的页面。
        if report.get("status") != "failed":
            report["status"] = "failed"
            report["errors"].append(str(error))

        try:
            save_report(report)
        except Exception as report_error:
            log(f"保存运行报告失败：{report_error}")

        log(f"运行失败：{error}")

        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"ERROR: {error}",
            file=sys.stderr,
        )
        raise
