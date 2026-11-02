
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.nakakumin.com/takenomaruchikusen/smartphone/"
OUT = Path("artifacts")
OUT.mkdir(exist_ok=True)


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
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            ),
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
            await page.screenshot(
                path=str(OUT / "01_official.png"),
                full_page=True,
            )

            # Find the official reservation button.
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
                raise RuntimeError(
                    "Official reservation button text not found."
                )

            # Click the button and handle either a popup
            # or navigation in the same tab.
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
                "step": "after_click",
                "url": reserve.url,
                "title": await reserve.title(),
            })

            report["final_url"] = reserve.url
            report["title"] = await reserve.title()

            report["body_excerpt"] = (
                await reserve.locator("body").inner_text()
            )[:10000]

            report["links"] = await reserve.locator("a").evaluate_all(
                """els => els.map(a => ({
                    text: (a.innerText || '').trim(),
                    href: a.href
                })).slice(0, 150)"""
            )

            await reserve.screenshot(
                path=str(OUT / "02_after_click.png"),
                full_page=True,
            )

        except Exception as e:
            report["errors"].append(
                type(e).__name__ + ": " + str(e)
            )

            try:
                report["body_excerpt"] = (
                    await page.locator("body").inner_text()
                )[:6000]

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
