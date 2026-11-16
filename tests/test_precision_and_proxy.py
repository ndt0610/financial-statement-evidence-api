from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from app.services.analysis_calculator import calculate_all
from app.services.financial_bridges import profit_bridge
from app.services.p11_narrative import build_p11_findings
from p5_fixture import pnj_book


def test_all_proxy_measures_explain_their_substitute():
    proxies=[m for m in calculate_all(pnj_book(),[2025]) if m.status=='PROXY']
    assert proxies
    assert all(m.reason and m.formula and m.evidences for m in proxies)
    reasons={m.metric_id:m.reason for m in proxies}
    assert 'bán chịu' in reasons['E03']
    assert 'mua chịu' in reasons['E04']
    assert 'vay dài hạn' in reasons['D03']
    assert 'khấu hao' in reasons['D07']
    assert 'thuế suất hiệu dụng' in reasons['P08']


def test_bridge_rounding_is_explicit_despite_callers_decimal_context():
    book=pnj_book()
    with localcontext() as context:
        context.rounding=ROUND_HALF_EVEN
        original=profit_bridge(book,2025)
    from decimal import ROUND_DOWN
    with localcontext() as context:
        context.rounding=ROUND_DOWN
        changed=profit_bridge(book,2025)
    assert original.steps[0].signed_impact==changed.steps[0].signed_impact
    assert original.target_delta==sum((s.signed_impact for s in original.steps),Decimal(0))+original.unexplained_residual
    assert 'half-even' in original.explanation


def test_narrative_money_includes_exact_source_amount():
    book=pnj_book()
    from app.services.full_analysis_report import build_full_report
    report=build_full_report(book,company='PNJ',years=[2024,2025])
    finding=next(f for f in report.analytical_findings if f['id']=='growth' and f['year']==2025)
    expected=book.get('revenue',2025).value_vnd
    assert f'{expected:,.0f}'.replace(',','.')+' đồng' in finding['narrative']
