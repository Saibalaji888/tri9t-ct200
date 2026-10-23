import fitz
from pathlib import Path

for filename in ["ct200_manual.pdf", "ct200_manual_v2.pdf"]:
    path = Path("data") / filename
    doc = fitz.open(path)

    print(f"\n{'=' * 60}")
    print(filename, "Pages:", len(doc))

    for page_number, page in enumerate(doc, start=1):
        print(f"\n--- PAGE {page_number} ---")
        print(page.get_text("text")[:2500])

        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if "lines" not in block:
                continue

            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text:
                        print(
                            f"font={span['size']:.1f}, "
                            f"bold={bool(span['flags'] & 16)}, "
                            f"text={text[:100]}"
                        )