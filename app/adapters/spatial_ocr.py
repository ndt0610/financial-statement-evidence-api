"""Conservatively reconstruct merged financial rows from OCR word boxes."""
import re


def recover_spatial_rows(cells, tokens, code_col, current_col, prior_col, refined_groups=()):
    """Return candidate cells only for a fully bounded, two-value row.

    Coordinates are PDF points in TOPLEFT origin. A code marks the beginning of
    a row and the next visible code bounds its end, accommodating wrapped labels.
    Missing, duplicated or ambiguous numbers are deliberately not repaired.
    """
    headers = {c['start_col_offset_idx']: c for c in cells
               if c.get('start_row_offset_idx') == 0 and c.get('bbox')}
    if not all(c in headers for c in (code_col, current_col, prior_col)):
        return []
    if current_col >= prior_col:
        return []
    def centre(box): return (box['l'] + box['r']) / 2
    boxes = [headers[c]['bbox'] for c in (code_col, current_col, prior_col)]
    if any(b.get('coord_origin') != 'TOPLEFT' for b in boxes): return []
    code_box, current_box, prior_box = boxes
    code_range = (code_box['l'] - 5, code_box['r'] + 5)
    split = (centre(current_box) + centre(prior_box)) / 2
    values = [c['bbox']['l'] for c in cells if c.get('start_col_offset_idx') == current_col
              and c.get('start_row_offset_idx', 0) > 0 and c.get('bbox', {}).get('coord_origin') == 'TOPLEFT']
    if not values: return []
    lower = min(values) - 4
    header_bottom = max(b['b'] for b in boxes)
    # Docling's merged code box limits the eligible rows and establishes that
    # the original table, not an adjacent table, is being repaired.
    merged = [c for c in cells if c.get('start_col_offset_idx') == code_col
              and len(str(c.get('text', '')).split()) > 1 and c.get('bbox', {}).get('coord_origin') == 'TOPLEFT']
    output = []
    for group_no, group in enumerate(merged):
        raw_codes = str(group['text']).split()
        code_refined = False
        if not all(re.fullmatch(r'\d{2,3}', code) for code in raw_codes): continue
        bounds = group['bbox']
        possible = [t for t in tokens if code_range[0] <= centre(t['bbox']) <= code_range[1]
                    and bounds['t']-4 <= (t['bbox']['t']+t['bbox']['b'])/2 <= bounds['b']+4
                    and re.fullmatch(r'\d{2,3}', t['text'].strip())]
        possible.sort(key=lambda t: t['bbox']['t'])
        # A targeted second OCR may repair a few digits only if it covers the
        # complete group, agrees elsewhere and every code stays in its row box.
        matches = [g for g in refined_groups if g.get('bbox') == bounds]
        if len(matches) == 1:
            refined = sorted(matches[0].get('tokens', []), key=lambda t:t['bbox']['t'])
            labels = [t.get('text','').strip() for t in refined]
            if (len(labels)==len(raw_codes) and len(set(labels))==len(labels)
                    and all(re.fullmatch(r'\d{2,3}',x) for x in labels)
                    and all(int(a)<int(b) for a,b in zip(labels,labels[1:]))
                    and sum(a==b for a,b in zip(labels,raw_codes)) >= (len(raw_codes)*9+9)//10
                    and all(float(t.get('score',0)) >= (.96 if name!=old else .8)
                            and code_range[0] <= centre(t['bbox']) <= code_range[1]
                            and bounds['t']-4 <= (t['bbox']['t']+t['bbox']['b'])/2 <= bounds['b']+4
                            for t,name,old in zip(refined,labels,raw_codes))):
                code_refined = labels != raw_codes
                raw_codes,possible=labels,refined
        seen = [t['text'].strip() for t in possible]
        if len(set(raw_codes)) != len(raw_codes) or len(set(seen)) != len(seen): continue
        positions = [raw_codes.index(value) if value in raw_codes else None for value in seen]
        known = [value for value in positions if value is not None]
        if known != sorted(known): continue
        group_row = group.get('start_row_offset_idx')
        year_boxes = [c['bbox'] for c in cells if c.get('start_row_offset_idx') == group_row
                      and c.get('start_col_offset_idx') in (current_col, prior_col)
                      and c.get('bbox', {}).get('coord_origin') == 'TOPLEFT']
        end_bound = min((b['b'] for b in year_boxes), default=bounds['b']) + 2
        for i, code in enumerate(possible):
            if positions[i] is None: continue
            if i+1 < len(possible) and (positions[i+1] is None or positions[i+1] != positions[i]+1): continue
            if i+1 == len(possible) and positions[i] != len(raw_codes)-1: continue
            if any(c.get('start_col_offset_idx') == code_col and str(c.get('text','')).strip() == code['text'].strip() for c in cells): continue
            top = max(header_bottom, code['bbox']['t'] - 2)
            bottom = possible[i+1]['bbox']['t'] - 2 if i+1 < len(possible) else end_bound
            candidates = [t for t in tokens if top <= (t['bbox']['t']+t['bbox']['b'])/2 < bottom
                          and centre(t['bbox']) >= lower and float(t.get('score', 0)) >= 0.8]
            years = [[t for t in candidates if (centre(t['bbox']) < split) == first]
                     for first in (True, False)]
            number_pattern = r'\(?-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?\)?|\(?-?\d+\)?'
            if any(len(group_) != 1 or not re.fullmatch(number_pattern, group_[0]['text'].strip()) for group_ in years):
                continue
            row = -1000000 - group_no * 10000 - i
            for col, token in ((code_col,code),(current_col,years[0][0]),(prior_col,years[1][0])):
                output.append({'start_row_offset_idx':row,'start_col_offset_idx':col,
                               'text':token['text'], 'bbox':token['bbox'], '_spatial_ocr':True,
                               '_code_refined':code_refined})
    return output
