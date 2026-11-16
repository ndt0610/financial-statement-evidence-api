/* PDF pre-scan review: page/form selection before the expensive conversion. */
(() => {
  const get = id => document.getElementById(id);
  const form = get('upload-form');
  if (!form) return;
  const panel = get('prescan-selection');
  const status = get('upload-message');
  const submit = form.querySelector('button[type="submit"]');
  let inventory = null;
  let choices = [];
  const formOptions = '<option value="B01-DN">B01 · Cân đối</option><option value="B02-DN">B02 · Kinh doanh</option><option value="B03-DN">B03 · Dòng tiền</option>';
  const regionOptions = '<option value="FULL">Toàn trang</option><option value="LEFT">Nửa trái</option><option value="RIGHT">Nửa phải</option><option value="TOP">Nửa trên</option><option value="BOTTOM">Nửa dưới</option>';
  const reasonLabel = {TITLE_OR_FORM:'Có tiêu đề/mẫu biểu', CONTINUATION_CODE_SEQUENCE:'Dãy mã tiếp nối',
    PRECEDING_PAGE_REVIEW:'Gợi ý trang trước · kiểm tra ảnh', FOLLOWING_PAGE_REVIEW:'Gợi ý trang sau · kiểm tra ảnh',
    BETWEEN_STATEMENTS_REVIEW:'Gợi ý trang giữa B01/B03 · có thể bị xoay'};
  const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const overlaps = (left, right) => left === right || left === 'FULL' || right === 'FULL' ||
    !((left === 'LEFT' && right === 'RIGHT') || (left === 'RIGHT' && right === 'LEFT') ||
      (left === 'TOP' && right === 'BOTTOM') || (left === 'BOTTOM' && right === 'TOP'));

  function draw() {
    if (!inventory) { panel.hidden = true; return; }
    panel.hidden = false;
    const unknown = inventory.pages.filter(p => p.route === 'IMAGE_REVIEW').length;
    const missing = inventory.missing_forms.length ? `Chưa tự nhận diện: ${inventory.missing_forms.join(', ')}. Kiểm tra các dòng gợi ý hoặc thêm trang thủ công.` : 'Đã có ứng viên cho cả ba bảng.';
    panel.innerHTML = `<div class="prescan-heading"><strong>Kiểm tra bản đồ trang · ${inventory.page_count} trang</strong><span>${unknown} trang ảnh cần xem trực tiếp</span></div><p class="hint">${safe(missing)} Chọn trang và loại bảng theo ảnh gốc trước khi trích xuất.</p><div class="prescan-list">${choices.map((choice, i) => {
      const page = inventory.pages[choice.page_number - 1];
      const preview = `/v1/documents/${encodeURIComponent(inventory.document_hash)}/prescan-page/${choice.page_number}`;
      const evidence = [reasonLabel[choice.reason] || choice.reason, choice.codes?.length ? `Mã ${choice.codes.slice(0, 6).join(', ')}` : '',
        choice.year_headers?.length ? `Kỳ ${choice.year_headers.join(' / ')}` : '',
        choice.report_scope === 'CONSOLIDATED' ? 'Hợp nhất' : choice.report_scope === 'SEPARATE' ? 'Riêng' : '',
        choice.unit_label, choice.confidence === 'LOW' ? 'Cần kiểm tra kỹ' : ''].filter(Boolean).join(' · ');
      return `<div class="prescan-row" data-index="${i}"><div class="prescan-thumb"><img loading="lazy" width="64" height="84" src="${preview}" alt="Ảnh trang ${choice.page_number}"><span class="prescan-region-mark" data-region="${safe(choice.region)}" aria-hidden="true"></span></div><div><strong>Trang ${choice.page_number}</strong><small>${safe(page.route === 'IMAGE_REVIEW' ? 'Trang ảnh · cần chọn bằng mắt' : evidence || 'Thêm thủ công')}</small><a href="${preview}" target="_blank" rel="noopener">Mở ảnh lớn ↗</a></div><label>Biểu mẫu<select class="prescan-form">${formOptions}</select></label><label>Vùng<select class="prescan-region">${regionOptions}</select></label><button type="button" class="prescan-remove" aria-label="Bỏ trang ${choice.page_number}">Bỏ</button></div>`;
    }).join('')}</div><div class="prescan-add"><label>Thêm trang<input id="prescan-page" type="number" min="1" max="${inventory.page_count}" placeholder="Số trang"></label><label>Biểu mẫu<select id="prescan-add-form">${formOptions}</select></label><label>Vùng<select id="prescan-add-region">${regionOptions}</select></label><button type="button" id="prescan-add-button" class="secondary">Thêm trang</button></div>`;
    panel.querySelectorAll('.prescan-row').forEach((row, i) => {
      row.querySelector('.prescan-form').value = choices[i].form_code;
      row.querySelector('.prescan-region').value = choices[i].region;
      row.querySelector('.prescan-form').addEventListener('change', e => { choices[i].form_code = e.target.value; });
      row.querySelector('.prescan-region').addEventListener('change', e => {
        choices[i].region = e.target.value;
        row.querySelector('.prescan-region-mark').dataset.region = e.target.value;
      });
      row.querySelector('.prescan-remove').addEventListener('click', () => { choices.splice(i, 1); draw(); });
    });
    get('prescan-add-button').addEventListener('click', () => {
      const page = Number(get('prescan-page').value);
      if (!Number.isInteger(page) || page < 1 || page > inventory.page_count) { status.textContent = 'Số trang phải nằm trong PDF đã quét.'; return; }
      if (choices.some(choice => choice.page_number === page && overlaps(choice.region, get('prescan-add-region').value))) {
        status.textContent = 'Trang và vùng này đã có trong bản đồ. Hãy sửa dòng hiện tại hoặc chọn nửa trang không trùng.';
        return;
      }
      choices.push({page_number: page, form_code: get('prescan-add-form').value, region: get('prescan-add-region').value, reason: 'Thêm thủ công'});
      draw();
    });
  }

  get('upload-file').addEventListener('change', () => {
    inventory = null; choices = []; panel.hidden = true; submit.textContent = 'Nạp vào hệ thống';
  });
  form.addEventListener('submit', async event => {
    const file = get('upload-file').files[0];
    if (!file || !file.name.toLowerCase().endsWith('.pdf')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    submit.disabled = true;
    try {
      if (!inventory) {
        status.textContent = 'Đang quét nhanh toàn bộ trang PDF…';
        const data = new FormData(); data.append('file', file);
        const response = await fetch('/v1/documents/prescan', {method:'POST', body:data});
        const result = await response.json();
        if (!response.ok) throw Error(JSON.stringify(result.detail || result));
        inventory = result;
        choices = result.candidates.map(item => ({page_number:item.page_number, form_code:item.form,
          region:item.region, reason:item.reason, codes:item.codes, year_headers:item.year_headers,
          report_scope:item.report_scope, unit_label:item.unit_label, confidence:item.confidence}));
        const pairs = [...new Set(result.candidates.map(item => item.year_headers || [])
          .filter(years => years.length === 2).map(years => [...years].sort().join(',')))];
        if (pairs.length === 1) get('upload-year').value = String(Math.max(...pairs[0].split(',').map(Number)));
        const scopes = [...new Set(result.candidates.map(item => item.report_scope).filter(Boolean))];
        if (scopes.length === 1) get('upload-scope').value = scopes[0];
        const units = [...new Set(result.candidates.map(item => item.unit_label).filter(Boolean))];
        if (units.length === 1) get('upload-unit').value = units[0];
        draw();
        submit.textContent = 'Xác nhận trang và trích xuất';
        status.textContent = `Đã quét ${result.page_count} trang. Xem ảnh và sửa bản đồ trang nếu cần.`;
      } else {
        if (!choices.length) throw Error('Chọn ít nhất một trang B01/B02/B03.');
        const year = Number(get('upload-year').value);
        const companyName = get('upload-name').value.trim();
        const companyId = get('upload-id').value.trim();
        const reviewer = get('upload-reviewer').value.trim();
        if (!Number.isInteger(year) || !companyName || !companyId || !reviewer) throw Error('Điền tên, mã, năm tài chính và người xác nhận.');
        const body = {prescan_id:inventory.prescan_id, company_name:companyName, company_id:companyId,
          report_scope:get('upload-scope').value, fiscal_start:`${year}-01-01`, fiscal_end:`${year}-12-31`,
          unit_label:get('upload-unit').value, reviewer, reviewed_page_map:true,
          pages:choices.map(({page_number,form_code,region}) => ({page_number,form_code,region})),
          ocr:get('upload-ocr').checked, extended_codes:true};
        status.textContent = 'Đang trích xuất các trang đã xác nhận…';
        const response = await fetch(`/v1/documents/${inventory.document_hash}/extract-confirmed`,
          {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
        const result = await response.json();
        if (!response.ok) throw Error(JSON.stringify(result.detail || result));
        status.textContent = `Đã trích xuất ${result.fact_count} dòng. Mở Hàng đợi hậu kiểm để kiểm tra số OCR.`;
        window.location.reload();
      }
    } catch (error) { status.textContent = `Không hoàn tất: ${error.message}`; }
    finally { submit.disabled = false; }
  }, true);
})();
