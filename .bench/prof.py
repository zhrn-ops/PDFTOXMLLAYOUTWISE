import cProfile, pstats, io
from pdf_to_jats.core.pdf_extractor import PDFExtractor
ex = PDFExtractor(use_docling=False)
pr = cProfile.Profile()
pr.enable()
d = ex.extract(".bench/big.pdf")
pr.disable()
s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
ps.print_stats(18)
print(s.getvalue())
