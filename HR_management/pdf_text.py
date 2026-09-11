"""Shared PDF reading helpers - PyMuPDF text layer extraction, with a
page-image fallback for scanned/photographed documents. Used by both
candidates/cv_extraction.py (reading CVs) and jobs/jd_extraction.py (reading
JD files), so the same tested logic backs both instead of two copies.
"""
import logging

logger = logging.getLogger(__name__)

MAX_VISION_PAGES = 4  # caps cost/latency on an unusually long scanned document


def extract_text(content):
    """The PDF's own embedded text layer - free, local, instant. Returns ''
    for a scanned/image-only PDF (no text layer to read), or a malformed one."""
    import fitz  # PyMuPDF - imported lazily; only needed when a file is actually read
    try:
        with fitz.open(stream=content, filetype='pdf') as doc:
            text = '\n'.join(page.get_text() for page in doc)
    except Exception as exc:  # noqa: BLE001 - a malformed PDF must fall back, not crash the caller
        logger.warning('Could not read the PDF text layer: %s', exc)
        return ''
    return text.strip()


def render_page_images(content, max_pages=MAX_VISION_PAGES):
    """Fallback for a scanned/image-only PDF: render each page to a PNG so a
    vision-capable model can read it directly instead of a text layer."""
    import fitz
    images = []
    with fitz.open(stream=content, filetype='pdf') as doc:
        for page in doc[:max_pages]:
            pixmap = page.get_pixmap(dpi=150)
            images.append(pixmap.tobytes('png'))
    return images
