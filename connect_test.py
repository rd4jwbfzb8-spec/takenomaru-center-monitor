
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.nakakumin.com/takenomaruchikusen/smartphone/"
OUT = Path("artifacts")
OUT.mkdir(exist_ok=True)


async def page_info(page):
    return {
        "url": page.url,
        "title": await page.title(),
        "body_excerpt": (
            await page.locator("body").inner_text()
        )[:15000],
        "interactive_elements": await page.locator(
            "button, input, select, [role=button], a"
        ).evaluate_all(
            """els => els.map(e => ({
                tag: e.tagName,
                text: (e.innerText || e.value || '').trim(),
                type: e.type || '',
                name: e.name || '',
                value: e.value || '',
                id: e.id || '',
                onclick: e.getAttribute('onclick') || '',
                href: e.href || ''
            })).slice(0, 200)"""
        ),
        "forms": await page.locator("form").evaluate_all(
            """els => els.map(f => ({
                action: f.action,
                method: f.method,
                inputs: Array.from(f.elements).map(e => ({
                    name: e.name,
                    type: e.type,
                    value: e.value
                }))
            }))"""
        )
    }


async def main():
    report = {
        "official_url": URL,
        "steps": [],
        "errors": [],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 390, "height": 844},
            locale="ja-JP",
        )
        page = await context.new_page()

        try:
            response = await page.goto(
                URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            report["steps"].append({
                "step": "open_official_site",
                "status": response.status if response else None,
                "url": page.url,
            })

            await page.wait_for_timeout(1500)

            target = page.get_by_text(
                "施設予約（Web予約システム）",
                exact=False,
            )

            if await target.count() == 0:
                raise RuntimeError("Reservation button not found")

            try:
                async with context.expect_page(timeout=12000) as popup:
                    await target.first.click(timeout=15000)
                reserve = await popup.value
            except Exception:
                reserve = page

            await reserve.wait_for_load_state(
                "domcontentloaded",
                timeout=60000,
            )
            await reserve.wait_for_timeout(1500)

            report["steps"].append({
                "step": "open_reservation_system",
                "url": reserve.url,
                "title": await reserve.title(),
            })

            report["page_before"] = await page_info(reserve)

            await reserve.screenshot(
                path=str(OUT / "01_reservation.png"),
                full_page=True,
            )

            # Locate the actual HTML input button.
            availability_button = reserve.locator(
                'input[type="button"][name="authority"]'
                '[value="空き状況からみる"]'
            )

            count = await availability_button.count()

            report["steps"].append({
                "step": "find_availability_button",
                "matches": count,
            })

            if count == 0:
                raise RuntimeError(
                    "空き状況からみる button not found"
                )

            await availability_button.first.click(timeout=15000)
            await reserve.wait_for_timeout(3000)

            report["steps"].append({
                "step": "click_availability_button",
                "status": "clicked",
                "url": reserve.url,
                "title": await reserve.title(),
            })

            report["availability_page"] = await page_info(
                reserve
            )

            await reserve.screenshot(
                path=str(OUT / "02_facility_selection.png"),
                full_page=True,
            )

        except Exception as e:
            report["errors"].append(
                type(e).__name__ + ": " + str(e)
            )

            try:
                report["last_page"] = await page_info(page)
                await page.screenshot(
                    path=str(OUT / "99_last_page.png"),
                    full_page=True,
                )
            except Exception:
                pass

        finally:
            (OUT / "report.json").write_text(
                json.dumps(
                    report,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
