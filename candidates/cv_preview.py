"""In-browser preview of Word (.docx) CVs.

Browsers can only show PDFs inline - a .docx CV just downloads, so the Edit
Candidate preview panel came up blank and "Open CV" seemed to do nothing.
This converts the .docx to a plain HTML page on our own server (mammoth), so
the CV never leaves the app. Old binary .doc files aren't supported by
mammoth and keep downloading as before.

The HTML comes from a candidate-supplied file, so the response carries a
strict Content-Security-Policy: no scripts at all (which also neutralises any
javascript: links in the document), only inline styles and embedded
data: images.
"""
import io
import logging
from urllib.parse import unquote, urlparse

import mammoth
from django.http import HttpResponse
from django.utils.html import escape

logger = logging.getLogger(__name__)

CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin: 0; background: #eef0f3; font-family: Calibri, "Segoe UI", Arial, sans-serif; color: #222; }}
  .bar {{ position: sticky; top: 0; display: flex; justify-content: space-between; align-items: center;
         gap: 12px; padding: 8px 16px; background: #fff; border-bottom: 1px solid #d9dce1; font-size: 13px; }}
  .bar a {{ color: #0b5cad; text-decoration: none; white-space: nowrap; }}
  .page {{ max-width: 816px; margin: 16px auto; padding: 48px 56px; background: #fff;
          box-shadow: 0 1px 4px rgba(0,0,0,.12); line-height: 1.45; font-size: 14px; overflow-wrap: anywhere; }}
  .page img {{ max-width: 100%; height: auto; }}
  .page table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
  .page td, .page th {{ border: 1px solid #ccc; padding: 4px 8px; vertical-align: top; }}
  .page h1, .page h2, .page h3 {{ margin: 1em 0 .4em; line-height: 1.2; }}
  .page p {{ margin: 0 0 .5em; }}
  @media (max-width: 700px) {{ .page {{ margin: 0; padding: 20px 16px; box-shadow: none; }} }}
</style></head>
<body>
<div class="bar"><span>{title} &middot; Word document preview (formatting simplified)</span>
<a href="{download_url}">Download original</a></div>
<div class="page">{body}</div>
</body></html>"""


def is_docx(name):
    """True if a stored file name or blob URL ends in .docx."""
    path = unquote(urlparse(name or '').path or (name or ''))
    return path.lower().endswith('.docx')


def docx_to_html(content):
    """Body HTML for the .docx bytes (mammoth escapes the document's text)."""
    result = mammoth.convert_to_html(io.BytesIO(content))
    return result.value


def docx_response(content, title, download_url):
    """A full HTML page showing the CV, or None if the file can't be converted
    (the caller then falls back to the plain download)."""
    try:
        body = docx_to_html(content)
    except Exception as exc:  # noqa: BLE001 - a bad file just downloads instead
        logger.warning('Could not convert Word CV to HTML (%s): %s', title, exc)
        return None
    if not body.strip():
        body = '<p><em>This Word document has no readable text. Use "Download original" to open it in Word.</em></p>'
    html = _PAGE.format(title=escape(title), download_url=escape(download_url), body=body)
    response = HttpResponse(html, content_type='text/html; charset=utf-8')
    response['Content-Security-Policy'] = CSP
    response['X-Content-Type-Options'] = 'nosniff'
    return response
