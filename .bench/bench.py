import time, fitz
doc = fitz.open()
lines_per_page = 120
pages = 12
for p in range(pages):
    page = doc.new_page()
    y = 50
    for i in range(lines_per_page):
        if i == 0:
            page.insert_text((72, y), "A Study of Something Important", fontsize=16)
        else:
            page.insert_text((72, y), f"This is body line number {i} with some words on page {p}.", fontsize=9)
        y += 12
path = ".bench/big.pdf"
doc.save(path)
doc.close()
print("wrote", path)

from pdf_to_jats.core.pdf_extractor import PDFExtractor
ex = PDFExtractor(use_docling=False)
t=time.perf_counter()
d = ex.extract(path)
print("extract secs", round(time.perf_counter()-t,3), "blocks", len(d.blocks))
