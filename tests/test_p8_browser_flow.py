"""Browser contract over a disposable copy of one real PNJ PDF index."""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
PNJ_2021 = "3f2ab86fdda300ae7058c651536b361bbe232107078d644c58d30781cc02c232"


def test_browser_audit_signoff_recalculation_and_evidence(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    source = ROOT / "data/uploads"
    original_index = source / "p8_signoff_backups_20261111" / f"{PNJ_2021}.index.json"
    needed = [source / f"{PNJ_2021}.pdf",
              original_index if original_index.is_file() else source / f"{PNJ_2021}.index.json"]
    if any(not p.is_file() for p in needed):
        pytest.skip("Real PNJ 2021 extraction is not available in this checkout")
    for file in needed:
        shutil.copy2(file, tmp_path / file.name)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [sys.executable, "-c",
               "import os; from pathlib import Path; import uvicorn; from app.api.main import create_app; "
               "uvicorn.run(create_app(upload_dir=Path(os.environ['P8_BROWSER_UPLOAD_DIR'])), "
               "host='127.0.0.1', port=int(os.environ['P8_BROWSER_PORT']), log_level='error')"]
    env = {**os.environ, "P8_BROWSER_UPLOAD_DIR": str(tmp_path), "P8_BROWSER_PORT": str(port)}
    server = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                with urlopen(base + "/audit", timeout=0.3) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("P8 browser test server did not start")
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(headless=True)
            except Exception as error:
                pytest.skip(f"Chromium is not installed for browser test: {error}")
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/audit", wait_until="networkidle")
                page.locator("#reviewer").fill("P8 browser reviewer")
                theme = page.locator('#audit-theme')
                before_theme = page.locator('html').get_attribute('class') or ''
                theme.click()
                assert (page.locator('html').get_attribute('class') or '') != before_theme
                for width in (375, 768, 1024, 1440):
                    page.set_viewport_size({'width': width, 'height': 900})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), width
                theme.click()
                before = page.locator("#rows .row").count()
                assert before >= 90
                page.locator("#rows .row").first.click()
                image = page.locator("#viewer img")
                image.wait_for()
                assert image.evaluate("img => img.naturalWidth") > 100
                assert "bbox" in page.locator("#detail").inner_text()
                page.locator("#confirm").click()
                page.get_by_text("Đã xác nhận", exact=False).first.wait_for()
                playwright.expect(page.locator("#rows .row")).to_have_count(before - 1)
                assert "52" in page.locator("#results").inner_text()
                page.locator("#batch-group").select_option("2021")
                assert "3 dòng đủ điều kiện duyệt nhóm" in page.locator("#batch-status").inner_text().lower()
                assert page.locator('.batch-row-choice:checked').count() == 0
                assert page.locator('.batch-row-choice:disabled').count() == 10
                page.locator('#batch-select-safe').click()
                assert page.locator('.batch-row-choice:checked').count() == 3
                page.locator("#batch-checked").check()
                page.locator("#batch-button").click()
                playwright.expect(page.locator("#rows .row")).to_have_count(before - 4)
                page.locator("#new-page").fill("68")
                page.locator("#open-page").click()
                proposal_image = page.locator("#viewer img")
                playwright.expect(proposal_image).to_be_visible()
                bounds = proposal_image.bounding_box()
                page.mouse.move(bounds["x"] + 50, bounds["y"] + 50)
                page.mouse.down()
                page.mouse.move(bounds["x"] + 100, bounds["y"] + 80)
                page.mouse.up()
                assert len(__import__("json").loads(page.locator("#new-bbox").input_value())) == 4
                page.goto(base + "/analysis", wait_until="networkidle")
                page.locator('[data-tab="metrics"]').click()
                # FY2020 comparative duration columns are now shown as a
                # separate, partially populated period alongside FY2021.
                assert page.locator("#metrics .metric-button").count() == 104
                page.locator("#metrics .metric-button").first.click()
                assert page.locator("#drawer").is_visible()
                assert page.locator("#drawer a[href*='/source#page=']").count() >= 1
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
