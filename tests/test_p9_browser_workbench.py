"""Real PNJ indexes through the five-tab financial workbench."""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from scripts.build_m4_fixture import build_fixture


ROOT = Path(__file__).resolve().parents[1]


def test_p9_real_pnj_five_tabs_period_units_bridges_and_tender(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    source = ROOT / "data/uploads"
    with TestClient(create_app(upload_dir=source)) as client:
        docs = [d for d in client.get("/v1/analysis/documents").json()["documents"]
                if d["company_id"] == "PNJ" and d["fiscal_year"] in (2023, 2024, 2025)]
    if len(docs) != 3:
        pytest.skip("PNJ 2023–2025 real indexes are unavailable locally")
    for d in docs:
        h = d["document_hash"]
        for suffix in (".index.json", ".pdf"):
            shutil.copy2(source / f"{h}{suffix}", tmp_path / f"{h}{suffix}")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [sys.executable, "-c",
               "import os; from pathlib import Path; import uvicorn; "
               "from app.api.main import create_app; "
               "uvicorn.run(create_app(upload_dir=Path(os.environ['P9_UPLOAD_DIR'])), "
               "host='127.0.0.1', port=int(os.environ['P9_PORT']), log_level='error')"]
    server = subprocess.Popen(command, cwd=ROOT,
                              env={**os.environ, "P9_UPLOAD_DIR": str(tmp_path), "P9_PORT": str(port)},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(70):
            try:
                with urlopen(base + "/analysis", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("P9 browser server did not start")
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(headless=True)
            except Exception as error:
                pytest.skip(f"Chromium unavailable: {error}")
            try:
                page = browser.new_page(viewport={"width": 1360, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)+'\n'+str(error.stack)))
                page.goto(base + "/analysis", wait_until="networkidle")
                assert "PNJ" in page.locator("#title").inner_text()
                assert page.locator("#pillars .pillar").count() == 5
                assert "#/tables/" not in page.locator("#pillars").inner_text()
                # The first report also supplies a comparative FY2022 column.
                assert page.locator("#executive thead th").count() == 5
                assert page.locator('#p11-charts svg').count() >= 3
                assert page.locator('#p11-charts .trend-chart path').count()>=3
                assert page.locator('#p11-charts .donut circle').count()==4
                assert 'Nợ ngắn hạn' in page.locator('.composition-legend').inner_text()
                point=page.locator('#p11-charts .trend-chart .chart-point').first
                point.focus();point.press('Enter')
                assert page.locator('#drawer').is_visible()
                assert 'Decimal nguyên bản' in page.locator('#drawer').inner_text()
                page.locator('#drawer-close').click()
                def chart_text_contrast():
                    return page.locator('#p11-charts .trend-chart svg text').first.evaluate("""el => {
                      const rgb=x=>x.match(/[\\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4});
                      const luminance=x=>{const c=rgb(x);return .2126*c[0]+.7152*c[1]+.0722*c[2]};
                      const a=luminance(getComputedStyle(el).fill),b=luminance(getComputedStyle(el.closest('.p11-chart')).backgroundColor);
                      return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
                    }""")
                assert chart_text_contrast() >= 4.5
                page.locator('.precision-note summary').click()
                assert 'chính xác đến đồng' in page.locator('.precision-note').inner_text()
                actual=page.evaluate("""() => [
                  formatExactMoney('9007199254740993','vnd'),
                  formatExactMoney('9007199254740993','billion'),
                  formatExactMoney('1.5','vnd'),formatExactMoney('2.5','vnd'),
                  formatExactMoney('-2.5','vnd'),formatDecimalHalfEven('2.345',2),
                  formatDecimalHalfEven('2.355',2),subtractDecimal('9007199254740993','9007199254740992'),
                  subtractDecimal('10.005','10.000'),multiplyDecimalPower10('0.02345',2)]""")
                assert actual==['9.007.199.254.740.993 đ','9.007.199,254740993 tỷ đồng',
                                '≈ 2 đ','≈ 2 đ','≈ -2 đ','2,34','2,36','1','0.005','2.345']
                assert 'Doanh thu' in page.locator('#p11-executive').inner_text()
                assert 'mở số để xem công thức' not in page.locator('#p10-tiers').inner_text()
                for role in ("investor", "credit", "tender", "cfo"):
                    page.locator(f'.p11-role-bar [data-role="{role}"]').click()
                    assert page.locator("#p10-kpis .p10-card").count() == 6
                    assert page.locator("#p10-tiers .p10-tier").count() == 4
                    if role=='cfo': assert 'Hàng tồn kho' in page.locator('#p11-charts').inner_text()
                    if role=='credit': assert 'dư nợ ngắn hạn' in page.locator('#p11-charts').inner_text()
                assert page.locator("html").evaluate("el => el.classList.contains('light')")
                page.locator("#p10-theme").click()
                assert not page.locator("html").evaluate("el => el.classList.contains('light')")
                assert chart_text_contrast() >= 4.5
                page.reload(wait_until='networkidle')
                assert not page.locator("html").evaluate("el => el.classList.contains('light')")
                assert chart_text_contrast() >= 4.5
                page.locator("#p10-theme").click()
                page.reload(wait_until='networkidle')
                assert page.locator("html").evaluate("el => el.classList.contains('light')")
                for tab in ("metrics", "bridges", "tender", "documents", "overview"):
                    page.locator(f'[data-tab="{tab}"]').click()
                    assert page.locator(f"#tab-{tab}").is_visible()
                page.locator("#period").select_option("2")
                playwright.expect(page.locator("#executive thead th")).to_have_count(3)
                assert "2024–2025" in page.locator("#subtitle").inner_text()
                page.locator("#period").select_option("custom")
                page.locator("#year-start").select_option("2023")
                page.locator("#year-end").select_option("2025")
                playwright.expect(page.locator("#executive thead th")).to_have_count(4)
                playwright.expect(page.locator("#subtitle")).to_contain_text("2023–2024–2025")
                before = page.locator("#pillars").inner_text()
                assert "tỷ đồng" in before
                page.locator("#unit").select_option("vnd")
                after = page.locator("#pillars").inner_text()
                assert " đ" in after and before != after
                page.locator('[data-tab="metrics"]').click()
                assert page.locator("#metrics tbody tr:not(.group)").count() == 52
                page.locator("#metric-filter").select_option("PROXY")
                assert 0 < page.locator("#metrics tbody tr:not(.group)").count() < 52
                page.locator('#metrics [data-id="E03"][data-year="2025"]').click()
                assert 'doanh thu bán chịu' in page.locator('#drawer').inner_text()
                page.locator('#drawer-close').click()
                page.locator("#metric-filter").select_option("all")
                page.locator('#metrics [data-id="G03"][data-year="2025"]').click()
                assert "DT_2023" in page.locator("#drawer").inner_text()
                page.locator("#drawer-close").click()
                page.locator("#metrics .metric-button").first.click()
                assert page.locator("#drawer").is_visible()
                assert "Decimal nguyên bản" in page.locator("#drawer").inner_text()
                page.locator("#drawer-close").click()
                page.locator('[data-tab="bridges"]').click()
                page.locator("#bridge-year").select_option("2025")
                assert '2024' in page.locator('#dupont').inner_text()
                assert '2025' in page.locator('#dupont').inner_text()
                assert page.locator('#p11-waterfall svg').count() == 1
                assert '715,58' in page.locator('.bridge-insight').inner_text()
                assert '1.512,64' in page.locator('.bridge-insight').inner_text()
                assert page.locator('.impact-row').count() == 9
                assert 'Biên gộp' in page.locator('.impact-row').first.inner_text()
                assert page.locator('.impact-bar.negative').count() > 0
                assert page.locator('.impact-bar.positive').count() > 0
                assert '1.512.637.148.600 đ' in page.locator('.impact-amount').first.get_attribute('title')
                page.locator("#bridge-year").select_option("2024")
                assert '141,82' in page.locator('.bridge-insight').inner_text()
                assert '2024' in page.locator('.impact-panel h3').inner_text()
                assert '2023' in page.locator('#dupont').inner_text()
                assert '2025' not in page.locator('#dupont').inner_text()
                page.locator("#bridge-year").select_option("2025")
                page.locator('[data-bridge="CFO"]').click()
                assert "Chênh lệch" not in page.locator("#bridge-detail").inner_text() or page.locator("#bridge-detail tbody tr").count() >= 9
                assert "CFO · B03:20" in page.locator("#bridge-detail").inner_text()
                page.locator('[data-bridge="PROFIT"]').click()
                assert "Chặng 1" in page.locator("#bridge-detail").inner_text()
                assert "Chặng 2" in page.locator("#bridge-detail").inner_text()
                page.locator('[data-tab="tender"]').click()
                page.locator("#tender-years").select_option("2")
                page.locator("#tender-form button[type=submit]").click()
                page.locator("#tender-results tbody tr").first.wait_for()
                assert page.locator("#tender-results tbody tr").count() == 7
                assert "Vốn lưu động ròng" in page.locator("#tender-quality").inner_text()
                page.locator("#p10-simulation button").click()
                playwright.expect(page.locator("#p10-simulation-result")).to_contain_text("Mô phỏng theo tham số")
                page.locator('[data-tab="documents"]').click()
                assert page.locator("#sources tbody tr").count() == 3
                assert "raw_materials" not in page.locator("main").inner_text()
                assert "DUPONT_NET_MARGIN" not in page.locator("main").inner_text()
                assert "B02-DN:#/tables/" not in page.locator("main").inner_text()
                workbook = build_fixture(tmp_path / "new_company.xlsx")
                page.locator("#upload-open").click()
                page.locator("#upload-file").set_input_files(str(workbook))
                assert not page.locator("#pdf-fields").is_visible()
                page.locator("#upload-id").fill("P9TEST")
                page.locator("#upload-form button[type=submit]").click()
                playwright.expect(page.locator("#upload-dialog")).not_to_be_visible()
                assert "P9TEST" in page.locator("#notice").inner_text()
                assert not errors, errors
                mobile=browser.new_page(viewport={"width":390,"height":844})
                mobile.goto(base+'/analysis',wait_until='networkidle')
                assert mobile.locator('#p11-charts svg').count() >= 3
                assert mobile.locator('.p11-role-bar button').count()==4
                assert mobile.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                mobile.close()
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
