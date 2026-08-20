"""Generate a known-good sample PDF for the capability probes.

The spec asks probes to run against "a bundled sample PDF". Generating it instead of
committing a binary keeps the repo text-only and makes the probe hermetic: no network,
no user data, and the same bytes on every platform.

The sample deliberately contains all three things the probes assert on:

* more than 200 characters of extractable text (``pdf_text``),
* a real embedded raster image (``figures``),
* a ``Figure 1:`` caption block directly beneath that image, so caption-pairing logic
  has something to pair.
"""

from __future__ import annotations

import zlib
from pathlib import Path

BODY = (
    "A Known-Good Sample Document\n\n"
    "This page exists so that lit-agent can verify text extraction without touching any "
    "of your own files. It carries enough prose to clear the two-hundred-character "
    "threshold that the pdf_text probe asserts on, and it is laid out as a single column "
    "so that extraction order is unambiguous on every platform.\n\n"
    "The coloured rectangle below is a genuine embedded raster image rather than a vector "
    "drawing, which is what the figures probe needs in order to prove that image "
    "extraction works end to end.\n"
)

CAPTION = "Figure 1: A small embedded raster image used by the figures probe."


def _swatch_png() -> bytes:
    """A tiny PNG, built byte by byte so nothing outside the stdlib is needed."""
    w = h = 24
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter byte: none
        for x in range(w):
            raw += bytes((40 + x * 8, 90 + y * 5, 200 - x * 4))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (len(data).to_bytes(4, "big") + tag + data
                + zlib.crc32(tag + data).to_bytes(4, "big"))
    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes((8, 2, 0, 0, 0))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def write_sample_pdf(dest: Path) -> Path:
    """Write the sample PDF to ``dest`` and return the path."""
    import pymupdf  # imported lazily so this module is importable without the venv

    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(60, 60, 540, 320), BODY, fontsize=11, fontname="helv")
    img_rect = pymupdf.Rect(60, 340, 210, 490)
    page.insert_image(img_rect, stream=_swatch_png())
    page.insert_textbox(pymupdf.Rect(60, 500, 540, 540), CAPTION, fontsize=10, fontname="helv")
    doc.save(str(dest), garbage=3, deflate=True)
    doc.close()
    return dest


def ensure_sample_pdf(cache_dir: Path) -> Path:
    """Return the cached sample PDF, generating it on first use."""
    dest = cache_dir / "sample.pdf"
    if not dest.is_file():
        write_sample_pdf(dest)
    return dest
