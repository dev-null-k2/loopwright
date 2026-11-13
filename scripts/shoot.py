"""Take the README screenshots from the static demo (docs/), so every picture
shows the real cockpit replaying a real recorded run.

    python -m http.server 8799 -d docs &      # serve the demo
    python scripts/shoot.py                   # needs: pip install playwright, and Chrome

All data on screen is the scripted model's output on invented examples.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

URL = os.environ.get("DEMO_URL", "http://127.0.0.1:8799/")
OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "media")
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None, args=["--no-sandbox"])
        errors: list[str] = []

        async def page_for(theme: str):
            page = await browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "404" not in m.text else None)   # the 404 is the probe for a live server
            await page.add_init_script(f"localStorage.setItem('loopwright.theme', '\"{theme}\"'); localStorage.setItem('loopwright.seen', 'true'); localStorage.setItem('loopwright.speed', '4');")
            await page.goto(URL)
            await page.wait_for_selector(".node")
            return page

        async def ask(page, text: str, wait: int = 9000):
            await page.fill("#chat-input", text)
            await page.press("#chat-input", "Enter")
            await page.wait_for_timeout(wait)

        for theme in ("light", "dark"):
            page = await page_for(theme)
            await ask(page, "Remember that Alex prefers morning meetings", 7000)
            await ask(page, "Book a catch-up with Alex on Friday")
            await page.click(".msg.agent:last-of-type .trail li:nth-child(3) .row")        # the memory search step
            await page.wait_for_timeout(700)
            await page.screenshot(path=f"{OUT}/cockpit-{theme}.png")
            await page.close()

        page = await page_for("light")
        await page.click("#left-mode button[data-mode=lessons]")
        await page.click(".lesson:nth-child(10) > button")
        await page.click(".lesson.open .run")
        await page.wait_for_timeout(9000)
        await page.click(".lesson.open ol li:first-child button")
        await page.wait_for_timeout(700)
        await page.screenshot(path=f"{OUT}/lesson-injection.png")
        for number, watch, name in ((20, 3, "lesson-browser"), (18, 1, "lesson-workflow")):
            await page.click("#left-mode button[data-mode=lessons]")
            await page.click(f".lesson:nth-child({number}) > button")
            await page.click(".lesson.open .run")
            await page.wait_for_timeout(11000)
            await page.click(f".lesson.open ol li:nth-child({watch}) button")
            await page.wait_for_timeout(900)
            await page.evaluate("document.getElementById('inspector').scrollTop = 330")     # bring the picture into view
            await page.wait_for_timeout(300)
            await page.screenshot(path=f"{OUT}/{name}.png")
            await page.click(f".lesson:nth-child({number}) > button")
        for tab, name in (("evals", "evals"), ("evals/compare", "compare"), ("automations/workflows", "automations"),
                          ("learn/retrieval-gate", "learn"), ("memory", "memory"), ("tools", "tools")):
            await page.goto(URL + "#" + tab)
            await page.wait_for_timeout(900)
            await page.screenshot(path=f"{OUT}/{name}.png")
        await browser.close()
        if errors:
            sys.exit("page errors: " + "; ".join(errors))
        print("screenshots written to docs/media")


asyncio.run(main())
