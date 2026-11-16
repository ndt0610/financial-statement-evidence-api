"""P6 note signoff, anchor checks, dashboard and second-company regression."""
import io
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.api.main import create_app
from app.adapters.supplemental_adapter import extract_note_candidates
from scripts.run_p6_vhc import run


def fixture_pdf():
    writer=PdfWriter()
    writer.add_blank_page(width=200,height=200)
    writer.add_blank_page(width=200,height=200)
    buffer=io.BytesIO();writer.write(buffer)
    return buffer.getvalue()


def cell(row,col,text):
    return {"start_row_offset_idx":row,"start_col_offset_idx":col,"text":text,
            "bbox":{"l":1,"t":1,"r":20,"b":20,"coord_origin":"TOPLEFT"}}


def converter(source,output,start,end,ocr):
    if start==1:
        cells=[cell(0,1,"Mã số"),cell(0,2,"Số cuối năm"),cell(0,3,"Số đầu năm")]
        for r,(label,code,now,prior) in enumerate((
            ("Tài sản ngắn hạn","100","1000","900"),
            ("Tiền và tương đương tiền","110","200","180"),
            ("Đầu tư tài chính ngắn hạn","120","100","90"),
            ("Tiền gửi có kỳ hạn","123","80","70"),
            ("Phải thu khách hàng","131","250","200"),
            ("Nợ ngắn hạn","310","500","450"),
            ("Vay ngắn hạn","320","150","140"),
            ("Vay dài hạn","338","0","0")),1):
            cells.extend((cell(r,0,label),cell(r,1,code),cell(r,2,now),cell(r,3,prior)))
        return {"status":"SUCCESS","document":{"texts":[{"text":"B01-DN báo cáo riêng đơn vị: đồng 2025", "prov":[{"page_no":1}]}],
            "tables":[{"prov":[{"page_no":1}],"data":{"table_cells":cells}}]}}
    return {"status":"SUCCESS","document":{"tables":[{"self_ref":"#/tables/note", "prov":[{"page_no":2}],"data":{"table_cells":[
        cell(0,0,"Đơn vị: đồng"),cell(0,1,"2025 VND"),cell(0,2,"2024 VND"),
        cell(1,0,"Tiền gửi có kỳ hạn"),cell(1,1,"40"),cell(1,2,"30")
    ]}}]}}


def test_supplemental_requires_review_and_reconciles(tmp_path):
    pdf=fixture_pdf()
    form={"company_name":"Synthetic","company_id":"SYN","report_scope":"SEPARATE",
          "fiscal_start":"2025-01-01","fiscal_end":"2025-12-31","page_start":"1","page_end":"1",
          "extended_codes":"true"}
    with TestClient(create_app(upload_dir=tmp_path,pdf_converter=converter)) as client:
        ingest=client.post('/v1/documents/ingest-pdf',data=form,files={"file":("sample.pdf",pdf,"application/pdf")})
        assert ingest.status_code==200,ingest.text
        key=ingest.json()['document_hash']
        extracted=client.post(f'/v1/documents/{key}/extract-supplemental',json={"page_start":2,"page_end":2,"fiscal_year":2025})
        assert extracted.status_code==200,extracted.text
        candidates=extracted.json()['candidates']
        assert len(candidates)==2 and candidates[0]['value_vnd']=='40'
        bad={**candidates[0],"concept":"term_deposits","parent_vas_code":"B01:110"}
        assert client.post(f'/v1/documents/{key}/supplemental-facts',json=bad).status_code==422
        proposed=client.post(f'/v1/documents/{key}/supplemental-facts',json=candidates[0])
        assert proposed.status_code==200,proposed.text
        fact=proposed.json();assert fact['state']=='EXTRACTED' and fact['reconciliation_status']=='PASS'
        before=client.get(f'/v1/documents/{key}/supplemental-facts').json()['facts']
        assert before[0]['state']=='EXTRACTED'
        body={"fact_id":fact['fact_id'],"reviewer":"Analyst","confirmed_value_vnd":"40",
              "reviewed_source_and_context":True,"decision":"CONFIRM"}
        assert client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,'confirmed_value_vnd':'41'}).status_code==409
        signed=client.post(f'/v1/documents/{key}/signoff-supplemental',json=body)
        assert signed.status_code==200,signed.text
        assert signed.json()['fact']['state']=='VERIFIED'
        assert client.post(f'/v1/documents/{key}/signoff-supplemental',json=body).status_code==409
        # The cash ratio is still a proxy until an eligible, signed cash figure exists.
        report=client.post('/v1/financial-analysis/full-report',json={'document_hashes':[key],'years':[2025]})
        assert report.status_code==200,report.text
        l03=next(m for m in report.json()['metrics'] if m['metric_id']=='L03' and m['fiscal_year']==2025)
        assert l03['status']=='PROXY'
        unrestricted={"concept":"unrestricted_cash","fiscal_year":2025,"value_vnd":"180",
                      "parent_vas_code":"B01:110","anchor_relation":"SUBSET_OF_PARENT",
                      "locator":{"source_kind":"pdf","page_number":2,"region":"cash note"},
                      "observed_text":"180"}
        u=client.post(f'/v1/documents/{key}/supplemental-facts',json=unrestricted)
        assert u.status_code==200,u.text
        pending=client.post('/v1/financial-analysis/full-report',json={'document_hashes':[key],'years':[2025]}).json()
        assert next(m for m in pending['metrics'] if m['metric_id']=='L03' and m['fiscal_year']==2025)['status']=='PROXY'
        u_id=u.json()['fact_id']
        confirmed=client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,'fact_id':u_id,'confirmed_value_vnd':'180'})
        assert confirmed.status_code==200,confirmed.text
        after=client.post('/v1/financial-analysis/full-report',json={'document_hashes':[key],'years':[2025]}).json()
        l03=next(m for m in after['metrics'] if m['metric_id']=='L03' and m['fiscal_year']==2025)
        assert l03['status']=='COMPUTED' and Decimal(l03['value'])==Decimal('0.36')
        assert {e['locator']['page_number'] for e in l03['evidences']}=={1,2}
        restricted_term=client.post(f'/v1/documents/{key}/supplemental-facts',json={
            "concept":"restricted_term_deposits","fiscal_year":2025,"value_vnd":"5",
            "parent_vas_code":"B01:123","anchor_relation":"SUBSET_OF_PARENT",
            "locator":{"source_kind":"pdf","page_number":2,"region":"pledged deposit"},
            "observed_text":"5"})
        assert restricted_term.status_code==200,restricted_term.text
        pre_term=client.post('/v1/financial-analysis/full-report',json={'document_hashes':[key],'years':[2025]}).json()
        assert next(m for m in pre_term['metrics'] if m['metric_id']=='D04')['status']=='PROXY'
        signed_term=client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,
            'fact_id':restricted_term.json()['fact_id'],'confirmed_value_vnd':'5'})
        assert signed_term.status_code==200 and signed_term.json()['cash_group_check']=='PASS'
        post_term=client.post('/v1/financial-analysis/full-report',json={'document_hashes':[key],'years':[2025]}).json()
        d04=next(m for m in post_term['metrics'] if m['metric_id']=='D04')
        assert d04['status']=='COMPUTED' and Decimal(d04['value'])==Decimal('-65')
        assert 'eligible_term_deposits@2025' in d04['inputs']
        def propose(concept,value,relation):
            response=client.post(f'/v1/documents/{key}/supplemental-facts',json={
                "concept":concept,"fiscal_year":2025,"value_vnd":value,"parent_vas_code":"B01:131",
                "anchor_relation":relation,"locator":{"source_kind":"pdf","page_number":2,"region":"receivables note"},
                "observed_text":value})
            assert response.status_code==200,response.text
            return response.json()['fact_id']
        gross=propose('gross_trade_receivables','300','SEMANTIC_ONLY')
        provision=propose('trade_receivable_provision','20','SUBSET_OF_PARENT')
        assert client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,'fact_id':gross,'confirmed_value_vnd':'300'}).status_code==200
        mismatch=client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,'fact_id':provision,'confirmed_value_vnd':'20'})
        assert mismatch.status_code==409 and 'reconcile' in mismatch.text.lower()
        guarded=client.post('/v1/financial-analysis/full-report',json={'document_hashes':[key],'years':[2025]}).json()
        q01=next(m for m in guarded['metrics'] if m['metric_id']=='Q01' and m['fiscal_year']==2025)
        assert q01['status']=='MISSING_INPUT'
        rejected=client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,'fact_id':provision,'confirmed_value_vnd':'20','decision':'REJECT'})
        assert rejected.status_code==200
        corrected=propose('trade_receivable_provision','50','SUBSET_OF_PARENT')
        reconciled=client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,'fact_id':corrected,'confirmed_value_vnd':'50'})
        assert reconciled.status_code==200 and reconciled.json()['receivable_group_check']=='PASS'
        wrong_relation=client.post(f'/v1/documents/{key}/supplemental-facts',json={
            "concept":"overdue_trade_receivables_gross","fiscal_year":2025,"value_vnd":"60",
            "parent_vas_code":"B01:131","anchor_relation":"SUBSET_OF_PARENT",
            "locator":{"source_kind":"pdf","page_number":2},"observed_text":"60"})
        assert wrong_relation.status_code==422
        oversized=propose('overdue_trade_receivables_gross','400','SEMANTIC_ONLY')
        assert client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,'fact_id':oversized,'confirmed_value_vnd':'400'}).status_code==409
        assert client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,'fact_id':oversized,'confirmed_value_vnd':'400','decision':'REJECT'}).status_code==200
        overdue=propose('overdue_trade_receivables_gross','60','SEMANTIC_ONLY')
        assert client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,'fact_id':overdue,'confirmed_value_vnd':'60'}).status_code==200
        related=propose('related_party_trade_receivables','40','SUBSET_OF_PARENT')
        assert client.post(f'/v1/documents/{key}/signoff-supplemental',json={**body,'fact_id':related,'confirmed_value_vnd':'40'}).status_code==200
        final_report=client.post('/v1/financial-analysis/full-report',json={'document_hashes':[key],'years':[2025]}).json()
        by_id={m['metric_id']:m for m in final_report['metrics'] if m['fiscal_year']==2025}
        for mid,expected in [('Q01',Decimal(50)/300*100),('Q02',Decimal(60)/300*100),('Q04',Decimal(40)/250*100)]:
            assert by_id[mid]['status']=='COMPUTED'
            assert Decimal(by_id[mid]['value'])==expected
            assert {e['locator']['page_number'] for e in by_id[mid]['evidences']}=={1,2}
            assert by_id[mid]['verification_status']=='EXTRACTED'
        live=client.post('/v1/analysis/company-report',json={'document_hashes':[key]})
        assert live.status_code==200,live.text
        assert len(live.json()['supplemental_facts'])==9
        assert client.get('/v1/analysis/documents').json()['documents'][0]['verified_supplemental']==7
        source=client.get(f'/v1/documents/{key}/source')
        assert source.status_code==200 and source.content==pdf
        assert client.get('/analysis').status_code==200
        assert client.get('/v1/financial-analysis/demo-reports').status_code==200
    with TestClient(create_app(upload_dir=tmp_path,pdf_converter=converter)) as client:
        persisted=client.get(f'/v1/documents/{key}/supplemental-facts').json()['facts']
        assert len(persisted)==9 and sum(item['state']=='VERIFIED' for item in persisted)==7


ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.skipif(not (ROOT/'.cache/holdout_new/vhc.pdf').exists(),reason='local issuer PDF unavailable')
def test_second_company_real_pdf_extracted_pipeline():
    result=run(ROOT/'.cache/holdout_new/vhc.pdf',ROOT/'.cache/holdout_new/vhc_docling.json')
    report=result['report']
    assert result['fact_count']>=90
    assert result['balance_residual_vnd']=={'2023':'0','2024':'0'}
    assert len(result['note_candidates'])==1
    pledged=result['note_candidates'][0]
    assert pledged['concept']=='restricted_term_deposits'
    assert Decimal(pledged['value_vnd'])==Decimal('60000000000')
    assert pledged['locator']['page_number']==73 and pledged['audit_status']=='EXTRACTED_PENDING_HUMAN_SIGNOFF'
    assert sum(report['coverage_by_year']['2024'].values())==52
    assert {b['bridge_id'] for b in report['bridges']}=={'PROFIT','CFO','FUNDING','CASH'}
    assert all(m['verification_status']=='EXTRACTED' for m in report['metrics'] if m['fiscal_year']==2024 and m['value'] is not None)
    p07=next(m for m in report['metrics'] if m['metric_id']=='P07' and m['fiscal_year']==2024)
    assert p07['status']=='COMPUTED' and Decimal(p07['value'])>0
    assert {e['locator']['page_number'] for e in p07['evidences']}=={64,65}
    with TestClient(create_app()) as client:
        source=client.get('/v1/financial-analysis/demo-sources/'+result['source_sha256'])
        assert source.status_code==200 and source.content.startswith(b'%PDF-')
        assert client.get('/v1/financial-analysis/demo-sources/'+'0'*64).status_code==404
        demo=client.get('/v1/financial-analysis/demo-reports/vhc-2024')
        assert demo.status_code==200 and len(demo.json()['note_candidates'])==1


@pytest.mark.skipif(not (ROOT/'.cache/pnj_precode/p6_notes_120_125.json').exists(),reason='local PNJ Docling note cache unavailable')
def test_real_pnj_note_candidates_exclude_substring_false_positives():
    payload=json.loads((ROOT/'.cache/pnj_precode/p6_notes_120_125.json').read_text(encoding='utf-8'))
    candidates,diagnostics=extract_note_candidates(payload,fiscal_year=2025)
    assert len(candidates)==11
    assert {item['locator']['page_number'] for item in candidates}=={121,123}
    assert {item['concept'] for item in candidates}=={'raw_materials','finished_goods','merchandise','term_deposits','inventory_in_transit','inventory_tools'}
    assert all(item['locator']['bounding_box'] for item in candidates)
    assert all('missing explicit VND unit' in message for message in diagnostics)
    with TestClient(create_app()) as client:
        shown=client.get('/v1/financial-analysis/demo-reports/pnj-2021-2025')
        assert shown.status_code==200
        assert len(shown.json()['note_candidates'])==8
        assert all(item['audit_status']=='EXTRACTED_PENDING_HUMAN_SIGNOFF' for item in shown.json()['note_candidates'])


def test_live_index_refreshes_after_signoff_from_another_app_instance(tmp_path):
    pdf=fixture_pdf()
    form={"company_name":"Synthetic","company_id":"SYN","report_scope":"SEPARATE",
          "fiscal_start":"2025-01-01","fiscal_end":"2025-12-31",
          "page_start":"1","page_end":"1","extended_codes":"true"}
    with (TestClient(create_app(upload_dir=tmp_path,pdf_converter=converter)) as first,
          TestClient(create_app(upload_dir=tmp_path,pdf_converter=converter)) as second):
        uploaded=first.post('/v1/documents/ingest-pdf',data=form,files={"file":("sample.pdf",pdf,"application/pdf")})
        assert uploaded.status_code==200,uploaded.text
        key=uploaded.json()['document_hash']
        proposed=first.post(f'/v1/documents/{key}/supplemental-facts',json={
            "concept":"term_deposits","fiscal_year":2025,"value_vnd":"40",
            "parent_vas_code":"B01:123","anchor_relation":"SUBSET_OF_PARENT",
            "locator":{"source_kind":"pdf","page_number":2},"observed_text":"40"})
        assert proposed.status_code==200,proposed.text
        assert first.get(f'/v1/documents/{key}/supplemental-facts').json()['facts'][0]['state']=='EXTRACTED'
        review=second.post(f'/v1/documents/{key}/signoff-supplemental',json={
            "fact_id":proposed.json()['fact_id'],"reviewer":"Owner","confirmed_value_vnd":"40",
            "reviewed_source_and_context":True,"decision":"CONFIRM"})
        assert review.status_code==200,review.text
        assert first.get(f'/v1/documents/{key}/supplemental-facts').json()['facts'][0]['state']=='VERIFIED'
