
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
        )[:12000],
        "links": await page.locator("a").evaluate_all(
            """els => els.map(a => ({
                text: (a.innerText || '').trim(),
                href: a.href
            })).slice(0, 150)"""
        ),
        "interactive_elements": await page.locator(
            "button, input, select, [role=button], "
            "a, img, area"
        ).evaluate_all(
            """els => els.map(e => ({
                tag: e.tagName,
                text: (e.innerText || e.value || '').trim(),
                type: e.type || '',
                name: e.name || '',
                id: e.id || '',
                role: e.getAttribute('role') || '',
                onclick: e.getAttribute('onclick') || '',
                href: e.href || ''
            })).slice(0, 200)"""
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

            count = await target.count()
            report["steps"].append({
                "step": "find_reservation_button",
                "matches": count,
            })

            if count == 0:
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
            await reserve.wait_for_timeout(2000)

            report["steps"].append({
                "step": "after_official_button_click",
                "url": reserve.url,
                "title": await reserve.title(),
            })

            report["reservation_page_before"] = await page_info(reserve)

            await reserve.screenshot(
                path=str(OUT / "01_reservation_menu.png"),
                full_page=True,
            )

            # Next step: click "空き照会・予約"
            menu = reserve.get_by_text(
                "空き照会・予約",
                exact=True,
            )

            menu_count = await menu.count()

            report["steps"].append({
                "step": "find_availability_menu",
                "matches": menu_count,
            })

            if menu_count > 0:
                try:
                    await menu.first.click(timeout=15000)
                    await reserve.wait_for_timeout(2500)

                    report["steps"].append({
                        "step": "click_availability_menu",
                        "status": "clicked",
                        "url": reserve.url,
                        "title": await reserve.title(),
                    })

                    report["availability_page"] = await page_info(
                        reserve
                    )

                    await reserve.screenshot(
                        path=str(OUT / "02_availability_menu.png"),
                        full_page=True,
                    )

                except Exception as e:
                    report["errors"].append(
                        "Availability menu click: "
                        + type(e).__name__ + ": " + str(e)
                    )
            else:
                report["availability_page"] = (
                    "空き照会・予約 menu not found"
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
