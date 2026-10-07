"""Regenerate an explicitly synthetic selectable PDF and matching OCR image."""
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "fixtures"


def create():
    OUT.mkdir(exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=520)
    page.draw_rect((0, 0, 600, 80), color=None, fill=(0.07, 0.17, 0.15))
    page.insert_text((36, 43), "SIMULATED DEMO - NOT AN ACTUAL RECORD", fontsize=19, color=(1, 1, 1))
    page.insert_text((36, 111), "Grain quality inspection / synthetic fixture", fontsize=16)
    for y, text in zip((170, 224, 278, 332, 386), ("Lot ID: LOT-007", "Quantity: 4500 kg", "Moisture: 12.0 %", "Temperature: 31 C", "Date: 2026-10-07")):
        page.insert_text((36, y), text, fontsize=24)
    font = Path("C:/Windows/Fonts/malgun.ttf")
    if font.exists():
        page.insert_font(fontname="malgun", fontfile=str(font))
        page.insert_text((36, 447), "가상 검사 문서 · 실제 입고 기록 아님", fontname="malgun", fontsize=17)
    page.insert_text((36, 490), "Review source evidence before any proposed record update.", fontsize=12)
    doc.subset_fonts()
    doc.save(OUT / "demo-check.pdf", garbage=4, deflate=True)
    page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False).save(OUT / "demo-check.png")
    doc.close()


if __name__ == "__main__":
    create()
