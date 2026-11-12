"""Local OCR (RapidOCR) returning text lines in PDF coordinates."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pymupdf

OCR_DPI = 200


@lru_cache(maxsize=1)
def _engine():
    import logging

    from rapidocr import RapidOCR

    # Page text is upright, so skip the text-angle classifier (~15% faster).
    engine = RapidOCR(params={"Global.use_cls": False})
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    return engine


def ocr_page(page: pymupdf.Page, dpi: int = OCR_DPI) -> list[dict]:
    """OCR a whole page. Returns [{text, bbox:[x0,y0,x1,y1] in PDF points, score}]."""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    img = np.ascontiguousarray(img[:, :, ::-1])  # RGB -> BGR (OpenCV convention)
    result = _engine()(img)
    if result is None or result.boxes is None:
        return []
    scale = 72.0 / dpi
    lines = []
    for box, text, score in zip(result.boxes, result.txts, result.scores):
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        bbox = [min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale]
        if text and text.strip():
            lines.append({"text": text.strip(), "bbox": [round(v, 1) for v in bbox], "score": round(float(score), 3)})
    return lines
