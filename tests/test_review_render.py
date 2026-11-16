"""Page preview is a visual aid, not an automated verification of evidence."""
import hashlib

import pytest
from pypdf import PdfWriter

pypdfium2 = pytest.importorskip("pypdfium2")
pytest.importorskip("PIL")

from scripts.render_review_page import render


def test_render_page_with_known_pdf_coordinates(tmp_path):
    pdf = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=100)
    writer.write(pdf)
    image = tmp_path / "page.png"
    result = render(pdf, 1, image, scale=1, grid_points=50)
    assert result["output_pixels"] == [200, 100]
    assert result["page_width_points"] == 200 and result["page_height_points"] == 100
    assert result["source_sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert image.read_bytes().startswith(b"\x89PNG")
    with pytest.raises(ValueError, match="already exists"):
        render(pdf, 1, image)
    with pytest.raises(ValueError, match="outside"):
        render(pdf, 2, tmp_path / "outside.png")
