"""Conservative numeric-note candidate extraction from Docling tables.

Only rows with explicit year headers, a known line anchor and one numeric cell
per year are accepted. Results are candidates and must be reviewed separately.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.models import PDFLocator
from app.services.vas_normalizer import fold, parse_accounting_number


@dataclass(frozen=True)
class NoteRule:
    concept: str
    label: str
    parent_vas_code: str
    relation: str


RULES = (
    NoteRule("term_deposits","tien gui co ky han","B01:123","SUBSET_OF_PARENT"),
    NoteRule("raw_materials","nguyen vat lieu","B01:141","SUBSET_OF_PARENT"),
    NoteRule("finished_goods","thanh pham","B01:141","SUBSET_OF_PARENT"),
    NoteRule("merchandise","hang hoa","B01:141","SUBSET_OF_PARENT"),
    NoteRule("work_in_progress","chi phi san xuat kinh doanh do dang","B01:141","SUBSET_OF_PARENT"),
    NoteRule("inventory_in_transit","hang dang di tren duong","B01:141","SUBSET_OF_PARENT"),
    NoteRule("real_estate_inventory","hang hoa bat dong san","B01:141","SUBSET_OF_PARENT"),
    NoteRule("inventory_tools","cong cu dung cu","B01:141","SUBSET_OF_PARENT"),
    NoteRule("consigned_inventory","hang gui di ban","B01:141","SUBSET_OF_PARENT"),
    NoteRule("store_rent","chi phi thue cua hang","B02:25","SUBSET_OF_PARENT"),
    NoteRule("pledged_assets_book_value","hang ton kho luan chuyen","B01:141","SUBSET_OF_PARENT"),
)

# Observed OCR variant, accepted only within a table with explicit VND and
# unique year columns. It remains a review candidate with its original bbox.
OCR_LABEL_ALIASES = {'nguyen vt liu': 'nguyen vat lieu', 'thanh phm': 'thanh pham',
                     'chi phi san xut kinh doanh d dang': 'chi phi san xuat kinh doanh do dang',
                     'hang dang di trn dung': 'hang dang di tren duong',
                     'hang hoa bt dng san': 'hang hoa bat dong san',
                     'cong cu dng cu': 'cong cu dung cu', 'hang gi di ban': 'hang gui di ban'}


def _source_bbox(cell, table):
    box=cell.get('bbox') or {}
    if not all(k in box for k in ('l','t','r','b','coord_origin')):
        return None, None
    coordinates=tuple(float(box[k]) for k in ('l','t','r','b'));origin=box['coord_origin']
    if table.get('_source_rotation')==90:
        if origin!='TOPLEFT' or not table.get('_source_original_height'):
            raise ValueError('Cannot map rotated note bbox')
        l,t,r,b=coordinates;height=float(table['_source_original_height'])
        coordinates=(t,height-r,b,height-l)
    return coordinates,origin


def extract_note_candidates(payload: dict, *, fiscal_year: int) -> tuple[list[dict], list[str]]:
    candidates=[];diagnostics=[]
    document=payload.get("document",{})
    page_context={}
    for item in document.get('texts',[]):
        for prov in item.get('prov',[]):
            page_context.setdefault(prov.get('page_no'),[]).append(fold(str(item.get('text',''))))
    for number,table in enumerate(document.get("tables",[])):
        prov=table.get("prov",[])
        if len(prov)!=1:
            diagnostics.append(f"note table {number}: ambiguous page")
            continue
        page=prov[0].get("page_no")
        cells=table.get("data",{}).get("table_cells",[])
        rows={}
        for cell in cells:
            r,c=cell.get("start_row_offset_idx"),cell.get("start_col_offset_idx")
            if isinstance(r,int) and isinstance(c,int):rows.setdefault(r,{})[c]=cell
        years={}
        header_text=" ".join(fold(str(cell.get("text",""))) for row,cols in rows.items() if row<=3 for cell in cols.values())
        # No silent multiplication from a note expressed in millions or thousands.
        if "trieu dong" in header_text or "nghin dong" in header_text or "ty dong" in header_text:
            diagnostics.append(f"note table {number}: unit conversion needs human review")
            continue
        if "vnd" not in header_text and "dong" not in header_text:
            diagnostics.append(f"note table {number}: missing explicit VND unit")
            continue
        for row,cols in rows.items():
            if row>3:continue
            for col,cell in cols.items():
                text=fold(str(cell.get("text","")))
                for year in (fiscal_year,fiscal_year-1):
                    if str(year) in text and ("vnd" in text or "dong" in text or text.strip()==str(year)):
                        years.setdefault(year,set()).add(col)
        if any(len(years.get(y,set()))!=1 for y in (fiscal_year,fiscal_year-1)):
            continue
        for row,cols in rows.items():
            label_columns=[col for col in cols if col not in years[fiscal_year] | years[fiscal_year-1]]
            if not label_columns:continue
            label=str(cols[min(label_columns)].get("text",""))
            normalized=" ".join(re.sub(r"[^a-z0-9 ]"," ",fold(label)).split())
            normalized=OCR_LABEL_ALIASES.get(normalized,normalized)
            matches=[rule for rule in RULES if normalized==rule.label]
            if normalized=='tin gi ngan hang co k hn':
                previous=' '.join(str(c.get('text','')) for c in rows.get(row-1,{}).values())
                if 'ngn hn' in fold(previous):
                    matches=[NoteRule('term_deposits',normalized,'B01:123','SUBSET_OF_PARENT')]
            context=' '.join(page_context.get(page,[]))
            if not matches and re.search(r'\b5\s+phai thu ngan han cua khach hang\b',context):
                if normalized=='ben thu ba':
                    matches=[NoteRule('third_party_trade_receivables','ben thu ba','B01:131','SUBSET_OF_PARENT')]
                elif re.fullmatch(r'ben lien quan thuyt minh 38 b',normalized):
                    matches=[NoteRule('related_party_trade_receivables','ben lien quan','B01:131','SUBSET_OF_PARENT')]
            if not matches and re.search(r'\b3\s+tien va cac khoan',context):
                cash_labels={'tin mt':'cash_on_hand','tin gi ngan hang khong k hn':'demand_deposits',
                             'cac khon turong duong tin':'cash_equivalents'}
                if normalized in cash_labels:
                    matches=[NoteRule(cash_labels[normalized],normalized,'B01:110','SUBSET_OF_PARENT')]
            if len(matches)!=1:continue
            rule=matches[0]
            # Some scans merge a gross amount and its provision into one OCR
            # cell; recover only a strict two-year gross/provision pair from an
            # explicitly labelled inventory table. Preserve the whole bbox.
            if rule.concept == 'finished_goods' and 'gia goc' in header_text and ('phong' in header_text or 'phong' in fold(' '.join(str(c.get('text','')) for c in cells))):
                paired={}
                pattern=r'^\s*(\d{1,3}(?:\.\d{3})+)\s*\((\d{1,3}(?:\.\d{3})+)\)\s*$'
                for year in (fiscal_year,fiscal_year-1):
                    col=next(iter(years[year]))
                    choices=[cell for cell in cols.values() if cell.get('start_col_offset_idx') in {col,col+1} and re.fullmatch(pattern,str(cell.get('text','')).strip())]
                    if len(choices)==1:paired[year]=choices[0]
                year_cols = [next(iter(years[y])) for y in (fiscal_year, fiscal_year-1)]
                if len(paired)==2 and abs(year_cols[0]-year_cols[1]) >= 2 and paired[fiscal_year] is not paired[fiscal_year-1]:
                    for year,cell in paired.items():
                        raw=str(cell['text']).strip(); matched=re.fullmatch(pattern,raw)
                        try:
                            coordinates,origin=_source_bbox(cell,table)
                            locator=PDFLocator(page_number=page,table_id=table.get('self_ref',f'note-{number}'),region=f'{label.strip()} cột {year}; ô gộp giá gốc và dự phòng',bounding_box=coordinates,coordinate_origin=origin)
                        except ValueError:
                            diagnostics.append(f'page {page} {rule.concept}: invalid bbox');continue
                        candidates.append({'concept':rule.concept,'fiscal_year':year,'value_vnd':str(parse_accounting_number(matched.group(1))),
                                           'parent_vas_code':rule.parent_vas_code,'anchor_relation':rule.relation,
                                           'locator':locator.model_dump(mode='json'),'observed_text':raw})
                    continue
                diagnostics.append(f'page {page} {rule.concept}: ambiguous gross/provision pairs')
                continue
            for year in (fiscal_year,fiscal_year-1):
                cell=cols.get(next(iter(years[year])))
                if cell is None:continue
                raw=str(cell.get("text"," ")).strip()
                try:value=parse_accounting_number(raw)
                except ValueError:
                    diagnostics.append(f"page {page} {rule.concept}: invalid number")
                    continue
                if value is None:continue
                try:
                    coordinates,origin=_source_bbox(cell,table)
                    locator=PDFLocator(page_number=page,table_id=table.get("self_ref",f"note-{number}"),
                                       region=f"{label.strip()} cột {year}",bounding_box=coordinates,
                                       coordinate_origin=origin)
                except ValueError:
                    diagnostics.append(f"page {page} {rule.concept}: invalid bbox")
                    continue
                candidates.append({"concept":rule.concept,"fiscal_year":year,"value_vnd":str(value),
                                   "parent_vas_code":rule.parent_vas_code,"anchor_relation":rule.relation,
                                   "locator":locator.model_dump(mode="json"),"observed_text":raw})
    # A narrowly templated disclosure can be recovered even when the 2-up
    # note table lacks an explicit unit or Docling emits it as running text.
    pledge_pattern=re.compile(
        r"tai ngay 31 thang 12 nam (20\d{2}), khoan tien gui ngan hang co ky han "
        r"voi so du la ([\d.,]+) dong duoc dung lam tai san the chap"
    )
    for number,paragraph in enumerate(document.get("texts",[])):
        prov=paragraph.get("prov",[])
        if len(prov)!=1:continue
        original=str(paragraph.get("text",""))
        normalized=" ".join(fold(original).split())
        matches=list(pledge_pattern.finditer(normalized))
        if len(matches)!=1:continue
        year=int(matches[0].group(1))
        if year not in {fiscal_year,fiscal_year-1}:continue
        raw=matches[0].group(2)
        try:value=parse_accounting_number(raw)
        except ValueError:continue
        if value is None or value<0:continue
        bbox=prov[0].get("bbox") or {}
        coordinates=tuple(float(bbox[k]) for k in ("l","t","r","b")) if all(k in bbox for k in ("l","t","r","b")) else None
        origin=bbox.get("coord_origin") if coordinates else None
        try:locator=PDFLocator(page_number=prov[0].get("page_no"),region=original,
                               table_id=f"text-{number}",bounding_box=coordinates,coordinate_origin=origin)
        except ValueError:continue
        candidates.append({"concept":"restricted_term_deposits","fiscal_year":year,"value_vnd":str(value),
                           "parent_vas_code":"B01:123","anchor_relation":"SUBSET_OF_PARENT",
                           "locator":locator.model_dump(mode="json"),"observed_text":raw})
    return candidates,diagnostics
