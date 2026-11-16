from app.adapters.spatial_ocr import recover_spatial_rows


def box(l,t,r,b): return {'l':l,'t':t,'r':r,'b':b,'coord_origin':'TOPLEFT'}
def cell(row,col,text,bbox): return {'start_row_offset_idx':row,'start_col_offset_idx':col,'text':text,'bbox':bbox}
def token(text,x,y): return {'text':text,'score':.99,'bbox':box(x,y,x+10,y+5)}


def test_wrapped_rows_are_bounded_by_next_code():
    cells=[cell(0,0,'Mã số',box(0,0,15,10)),cell(0,3,'2024',box(90,0,110,10)),
           cell(0,4,'2023',box(140,0,160,10)),cell(1,0,'100 110 120',box(0,20,15,80)),
           cell(1,3,'1 2 3',box(85,20,110,80)),cell(1,4,'4 5 6',box(135,20,160,80))]
    tokens=[token('100',2,20),token('110',2,40),token('120',2,60),
            token('1.000',90,30),token('2.000',90,45),token('3.000',90,65),
            token('4.000',140,30),token('5.000',140,45),token('6.000',140,65)]
    rows=recover_spatial_rows(cells,tokens,0,3,4)
    assert len(rows)==9
    assert [c['text'] for c in rows[:3]]==['100','1.000','4.000']
    assert len(recover_spatial_rows(cells,tokens+[token('9.000',90,30)],0,3,4))==6
    assert len(recover_spatial_rows(cells,[t for t in tokens if t['text']!='110'],0,3,4))==3
    misread=[{**t,'text':'999'} if t['text']=='110' else t for t in tokens]
    recovered=recover_spatial_rows(cells,misread,0,3,4)
    assert [c['text'] for c in recovered]==['120','3.000','6.000']


def test_no_recovery_if_year_column_layout_not_identified():
    assert recover_spatial_rows([],[],0,3,4)==[]


def test_refined_code_requires_consensus_and_preserves_unrelated_rows():
    codes=[f'{i:02d}' for i in range(1,21)]
    raw=codes.copy();raw[7]='80'
    bounds=box(0,20,15,425)
    cells=[cell(0,0,'Mã số',box(0,0,15,10)),cell(0,3,'2024',box(90,0,110,10)),
           cell(0,4,'2023',box(140,0,160,10)),cell(1,0,' '.join(raw),bounds),
           cell(1,3,'merged',box(85,20,110,425)),cell(1,4,'merged',box(135,20,160,425))]
    low=[token(code,2,20+i*20) for i,code in enumerate(raw)]
    refined=[token(code,2,20+i*20) for i,code in enumerate(codes)]
    amounts=[token('1.000',90,25+i*20) for i in range(20)]+[token('2.000',140,25+i*20) for i in range(20)]
    result=recover_spatial_rows(cells,low+amounts,0,3,4,[{'bbox':bounds,'tokens':refined}])
    assert any(x['text']=='08' and x['_code_refined'] for x in result)
    refined[7]['score']=.5
    result=recover_spatial_rows(cells,low+amounts,0,3,4,[{'bbox':bounds,'tokens':refined}])
    assert not any(x['text']=='08' for x in result)
