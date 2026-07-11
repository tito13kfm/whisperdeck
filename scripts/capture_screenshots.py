"""
capture_screenshots.py
======================

Drive the WhisperDesk browser via Playwright and capture screenshots
of all major pages, switching through all 4 UI themes.

Prereqs: pip install playwright && playwright install chromium
         A WhisperDesk server running on PORT (default 9782)
         A valid login (use screencap/screencap_pass_2026 or login inline)
"""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:9782"
USERNAME = "screencap"
PASSWORD = "screencap_pass_2026"
OUT = Path("screenshots")
OUT.mkdir(exist_ok=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        page.set_default_timeout(60000)

        # Login
        page.goto(f"{BASE}/")
        page.wait_for_load_state("networkidle")
        try:
            page.fill("#auth-user", USERNAME)
            page.fill("#auth-pass", PASSWORD)
            page.click("#auth-submit")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
        except Exception as e:
            print(f"Login: {e}")

        # Disable animations for cleaner screenshots
        page.add_style_tag(content="""
            *, *::before, *::after {
                animation-duration: 0s !important;
                animation-iteration-count: 1 !important;
                transition-duration: 0s !important;
            }
        """)

        def goto_nav(name):
            page.evaluate(f"""() => {{
                const btn = document.querySelector('[data-nav="{name}"]');
                if (btn) btn.click();
            }}""")
            page.wait_for_timeout(800)

        def shot(name, full_page=True, wait_ms=1500):
            page.wait_for_timeout(wait_ms)
            path = OUT / name
            page.screenshot(path=str(path), full_page=full_page)
            print(f"  -> {path} ({path.stat().st_size:,} bytes)")
            return path

        print("Capturing screenshots...")

        # 1. Monitor dashboard
        goto_nav("dashboard")
        shot("01-monitor.png", full_page=True)

        # 2. Transcribe page
        goto_nav("transcribe")
        shot("02-transcribe.png", full_page=True)

        # 3. Tape library
        goto_nav("transcripts")
        shot("03-tape-library.png", full_page=True)

        # 4. Transcript detail
        goto_nav("transcripts")
        page.wait_for_timeout(500)
        page.evaluate("""() => {
            const openBtn = document.querySelector('[data-act="open"]');
            if (openBtn) openBtn.click();
        }""")
        page.wait_for_timeout(2000)
        shot("04-transcript-detail.png", full_page=True)

        # 5. Corrected tab
        page.evaluate("""() => {
            const tabs = document.querySelectorAll('[data-tab], .tab, button');
            for (const t of tabs) {
                if (t.textContent && t.textContent.trim().toLowerCase() === 'corrected') {
                    t.click();
                    return;
                }
            }
        }""")
        page.wait_for_timeout(1500)
        shot("05-corrected.png", full_page=True)

        # 6. Queue
        goto_nav("queue")
        shot("06-queue.png", full_page=True)

        # 7. Voice roster
        goto_nav("voices")
        shot("07-voice-roster.png", full_page=True)

        # 8. Service panel (settings)
        goto_nav("settings")
        shot("08-service-panel.png", full_page=True)

        # 9. Hotwords - try to find a hotwords sub-tab in settings
        goto_nav("settings")
        page.wait_for_timeout(500)
        page.evaluate("""() => {
            const all = document.querySelectorAll('a, button, [role=tab], [data-tab]');
            for (const el of all) {
                const t = (el.textContent || '').trim().toLowerCase();
                if (t === 'hotwords' || t === 'hot word' || t === 'glossary') {
                    el.click();
                    return;
                }
            }
        }""")
        page.wait_for_timeout(1500)
        shot("09-hotwords.png", full_page=True)

        # 10. Theme screenshots - 4 faceplate themes
        # The app uses localStorage 'rack-theme' and applyTheme(name) function
        themes = ["charcoal", "silverface", "champagne", "blue-glass"]
        for theme in themes:
            try:
                # Set theme via localStorage and call applyTheme if available
                page.evaluate(f"""() => {{
                    localStorage.setItem('rack-theme', '{theme}');
                    if (typeof applyTheme === 'function') {{
                        applyTheme('{theme}');
                    }}
                }}""")
                # Reload to apply from localStorage
                page.goto(f"{BASE}/")
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(1500)
                # Navigate to monitor for the screenshot
                goto_nav("dashboard")
                path = OUT / f"10-theme-{theme}.png"
                page.screenshot(path=str(path), full_page=False)
                print(f"  -> {path} ({path.stat().st_size:,} bytes)")
            except Exception as e:
                print(f"  Theme {theme} failed: {e}")

        browser.close()
        print("\nDone!")


if __name__ == "__main__":
    main()
