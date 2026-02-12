import argparse
import csv
import re
from pathlib import Path
from typing import List, Tuple, Optional
import fitz  # PyMuPDF


def pdf_to_pngs(pdf_path: Path, out_dir: Path, dpi: int = 300) -> List[Tuple[Path, int]]:
    """
    Convert a PDF into PNG images at <dpi>. Returns list of (png_path, page_index).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    results = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_path = out_dir / f"{i+1:04d}.png"
        pix.save(str(png_path))
        results.append((png_path, i))
    doc.close()
    return results


def parse_positive_label_and_group(stem: str) -> Tuple[Optional[str], int]:
    """
    Extracts ('inventory'|'property_description', group_id) from file stem.
    Examples:
      inventory.pdf -> ('inventory', 0)
      inventory_2.pdf -> ('inventory', 2)
      property_description_1.pdf -> ('property_description', 1)
      property-description.pdf -> ('property_description', 0)
      description.pdf -> ('property_description', 0)   # permissive
    """
    s = stem.lower()

    # normalize dashes
    s = s.replace("-", "_").replace(" ", "_")

    # choose label
    label = None
    if s.startswith("inventory"):
        label = "inventory"
    elif s.startswith("property_description") or s.startswith("propertydescription") or s.startswith("description"):
        label = "property_description"

    # group id (suffix like _2)
    m = re.search(r"_(\d+)$", s)
    group = int(m.group(1)) if m else 0

    return label, group


def build_csv_row(rel_path: Path, subset: str, label: str, doc_id: str,
                  page_index: int, group_id: int) -> dict:
    row = {
        "image_path": str(rel_path).replace("\\", "/"),
        "subset": subset,                           # 'pages' or 'positives'
        "label": label,                             # 'page', 'inventory', 'property_description'
        "doc_id": doc_id,
        "page_index": page_index,
        "group_id": group_id,
        "label_inventory": 1 if label == "inventory" else 0,
        "label_property_description": 1 if label == "property_description" else 0,
    }
    return row


def main():
    ap = argparse.ArgumentParser(description="Convert deed PDFs to PNGs and create training CSV.")
    ap.add_argument("--input_root", required=True, help="Path to your data/ folder")
    ap.add_argument("--output_root", required=True, help="Where to write images/ and dataset.csv")
    ap.add_argument("--dpi", type=int, default=300, help="Rasterization DPI (default: 300)")
    args = ap.parse_args()

    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()
    pages_root = output_root / "images" / "pages"
    positives_root = output_root / "images" / "positives"
    csv_path = output_root / "dataset.csv"

    rows: List[dict] = []

    # 1) Find top-level deed PDFs in input_root (e.g., data/<doc_id>.pdf)
    deed_pdfs = [p for p in input_root.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]

    for deed_pdf in sorted(deed_pdfs):
        doc_id = deed_pdf.stem
        print(f"[PAGES] {doc_id}")

        # Convert parent deed PDF to page images
        out_dir = pages_root / doc_id
        page_imgs = pdf_to_pngs(deed_pdf, out_dir, dpi=args.dpi)
        for png_path, page_idx in page_imgs:
            rel = png_path.relative_to(output_root)
            rows.append(build_csv_row(rel, "pages", "page", doc_id, page_idx, group_id=0))

        # 2) Look for child positives in a folder named exactly like the doc_id
        child_dir = input_root / doc_id
        if child_dir.is_dir():
            for child_pdf in sorted(child_dir.glob("*.pdf")):
                label, group_id = parse_positive_label_and_group(child_pdf.stem)
                if label is None:
                    # Skip unrelated PDFs
                    continue

                print(f"  [POSITIVE] {doc_id} :: {child_pdf.name} -> {label} (group {group_id})")

                # Output folder: images/positives/<doc_id>/<label>/
                # If multi-page child PDF, include its basename in the filename.
                pos_out_dir = positives_root / doc_id / label
                pos_out_dir.mkdir(parents=True, exist_ok=True)

                doc = fitz.open(str(child_pdf))
                zoom = args.dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)

                for i, page in enumerate(doc):
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    # If there is more than one page, append page number
                    base = re.sub(r"[^A-Za-z0-9_]+", "_", child_pdf.stem)
                    if doc.page_count == 1:
                        out_name = f"{base}.png"
                    else:
                        out_name = f"{base}_{i+1:02d}.png"
                    out_png = pos_out_dir / out_name
                    pix.save(str(out_png))

                    rel = out_png.relative_to(output_root)
                    rows.append(
                        build_csv_row(rel, "positives", label, doc_id, page_index=i, group_id=group_id)
                    )
                doc.close()

    # 3) Write CSV
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "image_path", "subset", "label", "doc_id",
        "page_index", "group_id", "label_inventory", "label_property_description"
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"\nDone. Wrote {len(rows)} rows to {csv_path}")
    print(f"Images are under: {(output_root / 'images').as_posix()}")


if __name__ == "__main__":
    main()
