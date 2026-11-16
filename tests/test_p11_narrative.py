from app.services.full_analysis_report import build_full_report
from p5_fixture import pnj_book
from dataclasses import replace
from decimal import Decimal
from app.services.analysis_book import AnalysisBook
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.api.main import create_app


def test_pnj_divergence_and_cashflow_have_sourced_numbers():
    report=build_full_report(pnj_book(),company='PNJ',years=[2024,2025])
    findings={f['id']:f for f in report.analytical_findings}
    growth=findings['growth']
    assert '7,53%' in growth['narrative'] and '33,87%' in growth['narrative']
    # Differences use unrounded Decimal values, not subtraction of displayed ratios.
    assert '4,32 điểm %' in growth['narrative'] and '1,27 điểm %' in growth['narrative']
    assert '-502,29 tỷ đồng' in growth['narrative'] and '1.512,64 tỷ đồng' in growth['narrative']
    assert '-143,77 tỷ đồng' in growth['narrative']
    assert growth['evidences'] and 'revenue@2025' in growth['inputs']
    assert growth['headline']=='Doanh thu giảm nhưng lợi nhuận vẫn tăng'
    assert growth['meaning'] and growth['follow_up']
    cash=findings['cashflow']
    assert '0,67%' in cash['narrative'] and '18,89 tỷ đồng' in cash['narrative']
    assert '3,94%' in cash['narrative']
    assert '3.631,84' in cash['narrative'] and '-2.806,72' in cash['narrative']
    assert '-369,03' in cash['narrative'] and '-96,65' in cash['narrative']
    assert cash['evidences']
    assert cash['headline']=='Dòng tiền kinh doanh thấp hơn lợi nhuận'
    assert '23,06%' in findings['dupont']['narrative'] and '1,87 lần' in findings['dupont']['narrative']
    assert '8,98 điểm %' in findings['dupont']['narrative'] and '-6,31 điểm %' in findings['dupont']['narrative']
    assert '15.835,33' in findings['inventory']['narrative'] and '192,92' in findings['inventory']['narrative']
    assert '21,67%' in findings['inventory']['narrative'] and '13,44 tỷ đồng' in findings['inventory']['narrative']
    # Transcribed note components remain hidden until individually reviewed.
    assert '634,61' not in findings['inventory']['narrative'] and '4.820,47' not in findings['inventory']['narrative']
    assert '11.739,71' in findings['liquidity']['narrative'] and '2,71 lần' in findings['liquidity']['narrative']
    assert '74,14 %' in findings['liquidity']['narrative'] and '1.909,57' not in findings['liquidity']['narrative']


def test_single_year_does_not_invent_comparison():
    report=build_full_report(pnj_book(),company='PNJ',years=[2025])
    assert all(f['id'] not in {'growth','dupont'} for f in report.analytical_findings)
    assert any(f['id']=='cashflow' for f in report.analytical_findings)


def test_inventory_note_detail_appears_after_review():
    original=pnj_book()
    data=[replace(d,verification_status='HUMAN_VERIFIED') if d.concept=='raw_materials' else d for d in original.by_key.values()]
    report=build_full_report(AnalysisBook(data,analysis_years=[2024,2025]),company='PNJ',years=[2024,2025])
    finding=next(x for x in report.analytical_findings if x['id']=='inventory' and x['year']==2025)
    assert '634,61' in finding['narrative'] and '4.820,47' in finding['narrative']


def test_sign_change_uses_absolute_amounts_instead_of_growth_percent():
    original=pnj_book()
    data=[replace(d,value_vnd=Decimal('-100')) if d.concept=='net_income' and d.year==2024 else d for d in original.by_key.values()]
    report=build_full_report(AnalysisBook(data,analysis_years=[2024,2025]),company='PNJ',years=[2024,2025])
    text=next(f['narrative'] for f in report.analytical_findings if f['id']=='growth' and f['year']==2025)
    assert 'lợi nhuận sau thuế chuyển từ' in text


def test_nonpositive_income_has_no_cash_conversion_claim():
    original=pnj_book()
    data=[replace(d,value_vnd=Decimal('-1')) if d.concept=='net_income' and d.year==2025 else d for d in original.by_key.values()]
    report=build_full_report(AnalysisBook(data,analysis_years=[2024,2025]),company='PNJ',years=[2024,2025])
    finding=next(f for f in report.analytical_findings if f['id']=='cashflow' and f['year']==2025)
    assert 'không dương' in finding['meaning']


def test_vinh_hoan_real_source_golden_findings_when_available():
    root=Path(__file__).resolve().parents[1]/'data'/'uploads'
    if not root.is_dir(): pytest.skip('Local real PDF index unavailable')
    with TestClient(create_app(upload_dir=root)) as client:
        documents=client.get('/v1/analysis/documents').json()['documents']
        hashes=[d['document_hash'] for d in documents if d['company_id']=='VHC']
        if not hashes: pytest.skip('VHC index unavailable')
        response=client.post('/v1/analysis/company-report',json={'document_hashes':hashes,'include_comparative_periods':True})
        assert response.status_code==200
        facts={f['id']:f for f in response.json()['report']['analytical_findings'] if f['year']==2024}
        assert '24,72%' in facts['growth']['narrative'] and '33,78%' in facts['growth']['narrative']
        assert '159,14%' in facts['cashflow']['narrative'] and '1.025,79' in facts['cashflow']['narrative']
        assert '31,51%' in facts['inventory']['narrative'] and '23,83%' in facts['inventory']['narrative']
        assert '114,79 ngày' in facts['inventory']['narrative']
        assert '2.220,57' in facts['liquidity']['narrative'] and '60,00' in facts['liquidity']['narrative']


def test_cashflow_cumulative_uses_only_continuous_selected_years():
    report=build_full_report(pnj_book(),company='PNJ',years=[2021,2022,2023,2024,2025])
    cash=next(f['narrative'] for f in report.analytical_findings if f['id']=='cashflow' and f['year']==2025)
    assert '2021–2025' in cash and '10,13%' in cash
    report=build_full_report(pnj_book(),company='PNJ',years=[2021,2023,2025])
    cash=next(f['narrative'] for f in report.analytical_findings if f['id']=='cashflow' and f['year']==2025)
    assert 'Lũy kế' not in cash


def test_pnj_crosscheck_is_read_only_and_preserves_extracted():
    root=Path(__file__).resolve().parents[1]/'data'/'uploads'
    if not root.is_dir(): pytest.skip('Local PNJ indexes unavailable')
    with TestClient(create_app(upload_dir=root)) as client:
        documents=client.get('/v1/analysis/documents').json()['documents']
        hashes=[d['document_hash'] for d in documents if d['company_id']=='PNJ' and d['fiscal_year'] in (2024,2025)]
        if len(hashes)!=2: pytest.skip('PNJ 2024–2025 indexes unavailable')
        response=client.post('/v1/analysis/pnj-csv-crosscheck',json={'document_hashes':hashes})
        assert response.status_code==200, response.text
        rows=response.json()['rows']
        # Extended B01 extraction may add exact but still unreviewed rows.
        assert sum(r['match']=='EXACT' and r['state']=='EXTRACTED' for r in rows)>=90
        assert sum(r['match']=='DIFFERENT' for r in rows)==4


def test_pnj_live_five_year_golden_narratives():
    root=Path(__file__).resolve().parents[1]/'data'/'uploads'
    if not root.is_dir(): pytest.skip('Local PNJ indexes unavailable')
    with TestClient(create_app(upload_dir=root)) as client:
        documents=client.get('/v1/analysis/documents').json()['documents']
        hashes=[d['document_hash'] for d in documents if d['company_id']=='PNJ']
        if len(hashes)<5: pytest.skip('PNJ five-year indexes unavailable')
        response=client.post('/v1/analysis/company-report',json={'document_hashes':hashes,'years':[2021,2022,2023,2024,2025]})
        assert response.status_code==200,response.text
        f={x['id']:x for x in response.json()['report']['analytical_findings'] if x['year']==2025}
        for key,checks in {'growth':['7,53%','33,87%','-143,77'],
                           'cashflow':['76,49%','3,94%','0,67%','10,13%','-607,29','-117,89'],
                           'dupont':['23,06%','13,36%','15,14%','-6,31'],
                           'inventory':['78,53%','21,67%','13,44','3.475,32'],
                           'liquidity':['74,14','31,81','522,03','1.909,57']}.items():
            for text in checks: assert text in f[key]['narrative'], (key,text)
            assert f[key]['evidences']
