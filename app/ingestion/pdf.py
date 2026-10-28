from pathlib import Path

import pymupdf


class PDFExtractionError(Exception):
    pass


def extract_pages(path: Path) -> list[str]:
    """Return the plain text of every page, in order."""
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several exception types for bad files
        raise PDFExtractionError(f"Not a readable PDF: {exc}") from exc
    with doc:
        if doc.needs_pass:
            raise PDFExtractionError("PDF is password protected")
        return [page.get_text("text") for page in doc]
