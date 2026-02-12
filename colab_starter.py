#!!! CELL 1) apt install commands                                 #
###################################################################
## System packages (Colab = Ubuntu)                               #
## !apt-get -y update && apt-get -y install tesseract-ocr         #
## !pip install -q pillow pytesseract scikit-learn pandas joblib  #

#!!! CELL 2) Sanity checks (paths & versions)#
##############################################
import os, sys, shutil, PIL, pytesseract, pandas as pd, sklearn
from PIL import Image, ImageDraw, ImageFont

# Where is tesseract? ubuntu/linux commands#
############################################
# !which tesseract                         #
# !tesseract --version                     #


#Point pytesseract at the binary (usually not needed, but explicit is nice)#
pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract") or "/usr/bin/tesseract"
print("pytesseract using:", pytesseract.pytesseract.tesseract_cmd)

print("Pillow:", PIL.__version__)
print("pandas:", pd.__version__)
print("scikit-learn:", sklearn.__version__)

#!!! CELL 3) Mount Google Drive & set your dataset root#
########################################################
from google.colab import drive
drive.mount('/content/drive')

"""<<< EDIT THIS to your folder >>>"""

DATA_ROOT = "/content/drive/MyDrive/deeds/data/pages"
assert os.path.isdir(DATA_ROOT), f"Not found: {DATA_ROOT}"
print("DATA_ROOT =", DATA_ROOT)

# Peek a few doc folders#
for name in sorted(os.listdir(DATA_ROOT))[:5]:
    print("•", name)

#!!! CELL 4) Quick OCR smoke test (no dataset required)#
########################################################
# Make a tiny test image and OCR it                    #
img = Image.new("RGB", (900, 200), "white")
draw = ImageDraw.Draw(img)
text = 'THENCE N 89°31\'26" E a distance of 12.78 feet'
draw.text((20, 80), text, fill="black")  # default bitmap font is fine

display(img)
print("OCR:", pytesseract.image_to_string(img))

#!!! CELL 5) (Optional) Verify your folder structure & counts#
##############################################################
import glob

def summarize_dataset(root):
    docs = sorted([d for d in glob.glob(os.path.join(root, "doc_*")) if os.path.isdir(d)])
    print(f"Found {len(docs)} document folders")
    for d in docs[:10]:  # show first 10 to keep it short
        inv = os.path.join(d, "positives", "inventory")
        desc = os.path.join(d, "positives", "property_description")
        inv_count  = len(glob.glob(os.path.join(inv, "*.png"))) if os.path.isdir(inv) else 0
        desc_count = len(glob.glob(os.path.join(desc, "*.png"))) if os.path.isdir(desc) else 0
        page_imgs  = len(glob.glob(os.path.join(d, "*.png")))
        print(f"{os.path.basename(d)}  pages:{page_imgs:3d}  inventory:{inv_count:3d}  description:{desc_count:3d}")

summarize_dataset(DATA_ROOT)

#!!! CELL 6) OCR all pages → auto-label from your positives/ → write CSVs#
##########################################################################
import os, re, glob, hashlib, json, shutil
from collections import defaultdict
from PIL import Image
import pytesseract
import pandas as pd

# ---- config (uses DATA_ROOT from earlier cell) ----#
OUT_ROOT = "/content/drive/MyDrive/deeds/outputs"
CSV_DIR  = os.path.join(OUT_ROOT, "csv")
OCR_DIR  = os.path.join(OUT_ROOT, "ocr_cache")
os.makedirs(CSV_DIR, exist_ok=True)
os.makedirs(OCR_DIR, exist_ok=True)

def _cache_path(img_path:str)->str:
    # stable cache filename based on path
    key = os.path.relpath(img_path, start=DATA_ROOT)
    h = hashlib.md5(key.encode("utf-8")).hexdigest()
    return os.path.join(OCR_DIR, f"{h}.txt")

def ocr_image(img_path:str, force:bool=False)->str:
    cpath = _cache_path(img_path)
    if (not force) and os.path.exists(cpath):
        with open(cpath, "r", encoding="utf-8") as fh:
            return fh.read()
    # OCR (basic config; tweak psm/oem as needed)#
    try:
        txt = pytesseract.image_to_string(Image.open(img_path), config="--psm 6")
    except Exception as e:
        print(f"[WARN] OCR failed for {img_path}: {e}")
        txt = ""
    with open(cpath, "w", encoding="utf-8") as fh:
        fh.write(txt)
    return txt

def norm_text(s:str)->str:
    s = s.lower()
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9°'\".\-]+", " ", s)).strip()
    return s

def extract_page_number_from_name(path:str):
    """
    Try to parse a page index like 0001 from filenames such as 0001.png, 0012_inv.png, etc.
    Returns int or None.
    """
    base = os.path.basename(path)
    # prefer a 3–5 digit chunk (common for page numbers)#
    nums = re.findall(r"(?<!\d)(\d{3,5})(?!\d)", base)
    if not nums:
        return None
    try:
        return int(nums[0])
    except:
        return None

def guess_page_for_crop(crop_path:str, page_paths:list, page_texts_norm:dict):
    """
    Heuristic:
    1) If crop filename contains a page number and that exists, use it.
    2) Else OCR the crop and choose the page whose text contains the crop text,
       else pick the best fuzzy match by length overlap.
    """
    pn = extract_page_number_from_name(crop_path)
    if pn is not None:
        # try matching to any page whose number matches#
        for p in page_paths:
            pnum = extract_page_number_from_name(p)
            if pnum == pn:
                return p, "by_filename"
    # fallback: OCR and text containment
    ctext = norm_text(ocr_image(crop_path))
    if not ctext:
        return None, "no_text"
    # if crop text is short, fuzzy matching is noisy#
    # We'll look for containment if ctext >= 25 chars#
    best = (None, 0.0)
    for p in page_paths:
        ptxt = page_texts_norm[p]
        score = 0.0
        if len(ctext) >= 25 and ctext in ptxt:
            # strong signal#
            return p, "by_substring"
        # light-weight token overlap as a score#
        ptoks = set(ptxt.split())
        ctoks = set(ctext.split())
        if ctoks:
            overlap = len(ptoks & ctoks) / float(len(ctoks))
            score = overlap
        if score > best[1]:
            best = (p, score)
    # require at least modest overlap to avoid random picks#
    return (best[0], "by_overlap") if best[1] >= 0.15 else (None, "no_match")

def process_document(doc_dir:str):
    doc_id = os.path.basename(doc_dir)
    pages = sorted(glob.glob(os.path.join(doc_dir, "*.png")))
    inv_dir  = os.path.join(doc_dir, "positives", "inventory")
    desc_dir = os.path.join(doc_dir, "positives", "property_description")
    inv_crops  = sorted(glob.glob(os.path.join(inv_dir, "*.png")))  if os.path.isdir(inv_dir)  else []
    desc_crops = sorted(glob.glob(os.path.join(desc_dir, "*.png"))) if os.path.isdir(desc_dir) else []

    # OCR pages#
    page_texts = {}
    page_texts_norm = {}
    for p in pages:
        txt = ocr_image(p)
        page_texts[p] = txt
        page_texts_norm[p] = norm_text(txt)

    # Initialize labels#
    labels = {
        p: {
            "has_inventory": 0,
            "has_property_description": 0,
            "inventory_count": 0,
            "description_count": 0,
        }
        for p in pages
    }

    # Attach crops → pages#
    unmatched = {"inventory": [], "property_description": []}
    for kind, crop_list in [("inventory", inv_crops), ("property_description", desc_crops)]:
        for c in crop_list:
            page, how = guess_page_for_crop(c, pages, page_texts_norm)
            if page is None:
                unmatched[kind].append((c, how))
                continue
            lbl = labels[page]
            if kind == "inventory":
                lbl["has_inventory"] = 1
                lbl["inventory_count"] += 1
            else:
                lbl["has_property_description"] = 1
                lbl["description_count"] += 1

    # Build dataframe rows#
    rows = []
    for p in pages:
        rows.append({
            "doc_id": doc_id,
            "page_path": p,
            "page_name": os.path.basename(p),
            "page_index": extract_page_number_from_name(p),
            "has_inventory": labels[p]["has_inventory"],
            "has_property_description": labels[p]["has_property_description"],
            "inventory_count": labels[p]["inventory_count"],
            "description_count": labels[p]["description_count"],
            "text": page_texts[p],
        })
    df = pd.DataFrame(rows).sort_values(["doc_id","page_index","page_name"])
    csv_path = os.path.join(CSV_DIR, f"{doc_id}.csv")
    df.to_csv(csv_path, index=False)
    return df, unmatched

# ---- run over all docs ----#
doc_dirs = sorted([d for d in glob.glob(os.path.join(DATA_ROOT, "doc_*")) if os.path.isdir(d)])
print(f"Found {len(doc_dirs)} doc folders")

master = []
all_unmatched = []
for i, d in enumerate(doc_dirs, 1):
    print(f"[{i}/{len(doc_dirs)}] {os.path.basename(d)}")
    df, um = process_document(d)
    master.append(df)
    for k in ("inventory","property_description"):
        for tup in um[k]:
            all_unmatched.append({"doc": os.path.basename(d), "kind": k, "crop": tup[0], "reason": tup[1]})

master_df = pd.concat(master, ignore_index=True) if master else pd.DataFrame()
master_csv = os.path.join(CSV_DIR, "_master_pages.csv")
master_df.to_csv(master_csv, index=False)

print("\nWrote per-doc CSVs to:", CSV_DIR)
print("Master CSV:", master_csv)
print("Total rows:", len(master_df))
print("Unmatched crops:", len(all_unmatched))
if all_unmatched:
    # save a quick log so you can inspect and rename/move any tricky crops#
    um_csv = os.path.join(CSV_DIR, "_unmatched_crops.csv")
    pd.DataFrame(all_unmatched).to_csv(um_csv, index=False)
    print("Unmatched detail:", um_csv)

#!!! CELL 7) Peek results quickly#
##################################
import pandas as pd, os

master_csv = os.path.join(CSV_DIR, "_master_pages.csv")
df = pd.read_csv(master_csv)

print("Rows:", len(df))
print("\nLabel counts (pages with ≥1 crop mapped):")
print(df[["has_inventory","has_property_description"]].sum())

print("\nSample positives:")
display(df[(df.has_inventory==1) | (df.has_property_description==1)].head(8))

print("\nSample negatives:")
display(df[(df.has_inventory==0) & (df.has_property_description==0)].head(8))

#!!! CELL 8) Train a simple TF-IDF → LogisticRegression baseline (grouped split by doc)#
########################################################################################
import os, pandas as pd, numpy as np
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, roc_auc_score, confusion_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from joblib import dump

# Uses CSV_DIR from earlier cell; fallback if needed#
if 'CSV_DIR' not in globals():
    CSV_DIR = "/content/drive/MyDrive/deeds/outputs/csv"
master_csv = os.path.join(CSV_DIR, "_master_pages.csv")

df = pd.read_csv(master_csv)
df["text"] = df["text"].fillna("")

# features/labels
X_text = df["text"].values
y_inv  = df["has_inventory"].astype(int).values
y_desc = df["has_property_description"].astype(int).values
groups = df["doc_id"].astype(str).values

# group-wise train/test split (avoid leakage across pages of same deed)
gss = GroupShuffleSplit(test_size=0.2, n_splits=1, random_state=42)
train_idx, test_idx = next(gss.split(X_text, y_inv, groups=groups))

def make_pipeline():
    return Pipeline([
        ("tfidf", TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            ngram_range=(1,2),
            max_df=0.9,
            min_df=2,
            sublinear_tf=True
        )),
        ("clf", LogisticRegression(
            max_iter=200,
            class_weight="balanced",
            solver="liblinear"  # robust for sparse + class_weight
        ))
    ])

inv_pipe  = make_pipeline()
desc_pipe = make_pipeline()

# fit
inv_pipe.fit(X_text[train_idx],  y_inv[train_idx])
desc_pipe.fit(X_text[train_idx], y_desc[train_idx])

# eval
def eval_binary(pipe, X_tr, y_tr, X_te, y_te, name):
    yhat_tr = pipe.predict(X_tr)
    yhat_te = pipe.predict(X_te)

    # proba for ROC AUC (guard if not available)
    try:
        p_te = pipe.predict_proba(X_te)[:,1]
        auc = roc_auc_score(y_te, p_te)
    except Exception:
        p_te = None
        auc = None

    print(f"\n=== {name} ===")
    print("Train report:\n", classification_report(y_tr, yhat_tr, digits=3))
    print("Test  report:\n", classification_report(y_te, yhat_te, digits=3))
    if auc is not None:
        print(f"Test ROC AUC: {auc:.3f}")
    print("Confusion matrix (test):\n", confusion_matrix(y_te, yhat_te))

eval_binary(inv_pipe,  X_text[train_idx], y_inv[train_idx],  X_text[test_idx], y_inv[test_idx],  "INVENTORY")
eval_binary(desc_pipe, X_text[train_idx], y_desc[train_idx], X_text[test_idx], y_desc[test_idx], "PROPERTY_DESCRIPTION")

# save models
MODEL_DIR = os.path.join(os.path.dirname(CSV_DIR), "models")
os.makedirs(MODEL_DIR, exist_ok=True)
dump(inv_pipe,  os.path.join(MODEL_DIR, "inv_pipe.joblib"))
dump(desc_pipe, os.path.join(MODEL_DIR, "desc_pipe.joblib"))

print("\nSaved models to:", MODEL_DIR)


#!!! CELL 9) Inference helper (run on any doc_xxx/ folder of page PNGs)#
########################################################################
import glob, os
from joblib import load
import pandas as pd
import numpy as np

# expects: DATA_ROOT, ocr_image, extract_page_number_from_name from earlier cells
MODEL_DIR = os.path.join(os.path.dirname(CSV_DIR), "models")
inv_pipe  = load(os.path.join(MODEL_DIR, "inv_pipe.joblib"))
desc_pipe = load(os.path.join(MODEL_DIR, "desc_pipe.joblib"))

def predict_doc(doc_dir, inv_model, desc_model, prob_threshold=0.5):
    doc_id = os.path.basename(doc_dir.rstrip("/"))
    pages = sorted(glob.glob(os.path.join(doc_dir, "*.png")))
    if not pages:
        raise ValueError(f"No page images found in {doc_dir}")

    texts, pnums = [], []
    for p in pages:
        texts.append(ocr_image(p))
        pnums.append(extract_page_number_from_name(p))

    X = np.array(texts)
    inv_p  = inv_model.predict_proba(X)[:,1]
    desc_p = desc_model.predict_proba(X)[:,1]
    inv_y  = (inv_p  >= prob_threshold).astype(int)
    desc_y = (desc_p >= prob_threshold).astype(int)

    df_pred = pd.DataFrame({
        "doc_id": doc_id,
        "page_path": pages,
        "page_index": pnums,
        "inv_prob": inv_p,
        "desc_prob": desc_p,
        "pred_inventory": inv_y,
        "pred_property_description": desc_y,
    }).sort_values(["page_index","page_path"]).reset_index(drop=True)

    def contiguous_runs(mask_series, page_series):
        """Return list of (start_idx, end_idx, [page_numbers]) for True spans."""
        runs = []
        start = None
        pages_acc = []
        prev = None
        for flag, pg in zip(mask_series.tolist(), page_series.tolist()):
            if flag:
                if start is None:  # start new run
                    start = pg
                    pages_acc = [pg]
                else:
                    # check contiguity by +1 if page numbers exist, else just accumulate
                    if (prev is not None) and (pg is not None) and (prev is not None) and (pg == prev + 1):
                        pages_acc.append(pg)
                    else:
                        pages_acc.append(pg)
                prev = pg
            else:
                if start is not None:
                    runs.append({
                        "start_page": pages_acc[0],
                        "end_page": pages_acc[-1],
                        "pages": pages_acc.copy()
                    })
                start, pages_acc, prev = None, [], None
        if start is not None:
            runs.append({
                "start_page": pages_acc[0],
                "end_page": pages_acc[-1],
                "pages": pages_acc.copy()
            })
        return runs

    inv_runs  = contiguous_runs(df_pred["pred_inventory"], df_pred["page_index"])
    desc_runs = contiguous_runs(df_pred["pred_property_description"], df_pred["page_index"])

    result = {
        "doc_id": doc_id,
        "inventory_runs": inv_runs,
        "description_runs": desc_runs,
    }
    return df_pred, result

# Example: run on the first document folder you have
if 'DATA_ROOT' in globals():
    doc_dirs = sorted([d for d in glob.glob(os.path.join(DATA_ROOT, "doc_*")) if os.path.isdir(d)])
    if doc_dirs:
        sample_doc = doc_dirs[0]
        print("Predicting on:", os.path.basename(sample_doc))
        pred_df, segments = predict_doc(sample_doc, inv_pipe, desc_pipe, prob_threshold=0.5)
        display(pred_df.head(10))
        print("\nSegments (page runs):", segments)

#!!! CELL 9) Inference helper (run on any doc_xxx/ folder of page PNGs)#
########################################################################
import glob, os
from joblib import load
import pandas as pd
import numpy as np

# expects: DATA_ROOT, ocr_image, extract_page_number_from_name from earlier cells
MODEL_DIR = os.path.join(os.path.dirname(CSV_DIR), "models")
inv_pipe  = load(os.path.join(MODEL_DIR, "inv_pipe.joblib"))
desc_pipe = load(os.path.join(MODEL_DIR, "desc_pipe.joblib"))

def predict_doc(doc_dir, inv_model, desc_model, prob_threshold=0.5):
    doc_id = os.path.basename(doc_dir.rstrip("/"))
    pages = sorted(glob.glob(os.path.join(doc_dir, "*.png")))
    if not pages:
        raise ValueError(f"No page images found in {doc_dir}")

    texts, pnums = [], []
    for p in pages:
        texts.append(ocr_image(p))
        pnums.append(extract_page_number_from_name(p))

    X = np.array(texts)
    inv_p  = inv_model.predict_proba(X)[:,1]
    desc_p = desc_model.predict_proba(X)[:,1]
    inv_y  = (inv_p  >= prob_threshold).astype(int)
    desc_y = (desc_p >= prob_threshold).astype(int)

    df_pred = pd.DataFrame({
        "doc_id": doc_id,
        "page_path": pages,
        "page_index": pnums,
        "inv_prob": inv_p,
        "desc_prob": desc_p,
        "pred_inventory": inv_y,
        "pred_property_description": desc_y,
    }).sort_values(["page_index","page_path"]).reset_index(drop=True)

    def contiguous_runs(mask_series, page_series):
        """Return list of (start_idx, end_idx, [page_numbers]) for True spans."""
        runs = []
        start = None
        pages_acc = []
        prev = None
        for flag, pg in zip(mask_series.tolist(), page_series.tolist()):
            if flag:
                if start is None:  # start new run
                    start = pg
                    pages_acc = [pg]
                else:
                    # check contiguity by +1 if page numbers exist, else just accumulate
                    if (prev is not None) and (pg is not None) and (prev is not None) and (pg == prev + 1):
                        pages_acc.append(pg)
                    else:
                        pages_acc.append(pg)
                prev = pg
            else:
                if start is not None:
                    runs.append({
                        "start_page": pages_acc[0],
                        "end_page": pages_acc[-1],
                        "pages": pages_acc.copy()
                    })
                start, pages_acc, prev = None, [], None
        if start is not None:
            runs.append({
                "start_page": pages_acc[0],
                "end_page": pages_acc[-1],
                "pages": pages_acc.copy()
            })
        return runs

    inv_runs  = contiguous_runs(df_pred["pred_inventory"], df_pred["page_index"])
    desc_runs = contiguous_runs(df_pred["pred_property_description"], df_pred["page_index"])

    result = {
        "doc_id": doc_id,
        "inventory_runs": inv_runs,
        "description_runs": desc_runs,
    }
    return df_pred, result

# Example: run on the first document folder you have
if 'DATA_ROOT' in globals():
    doc_dirs = sorted([d for d in glob.glob(os.path.join(DATA_ROOT, "doc_*")) if os.path.isdir(d)])
    if doc_dirs:
        sample_doc = doc_dirs[0]
        print("Predicting on:", os.path.basename(sample_doc))
        pred_df, segments = predict_doc(sample_doc, inv_pipe, desc_pipe, prob_threshold=0.5)
        display(pred_df.head(10))
        print("\nSegments (page runs):", segments)

#!!! CELL 10) (Optional) Pair inventories to descriptions in reading order#
###########################################################################
def pair_inventory_to_descriptions(inv_runs, desc_runs):
    pairs = []
    for inv in inv_runs:
        inv_start = inv["start_page"]
        attached = [d for d in desc_runs if (d["start_page"] is None) or (inv_start is None) or (d["start_page"] >= inv_start)]
        pairs.append({"inventory": inv, "descriptions": attached})
    return pairs

# Example post-processing:
if 'segments' in locals():
    pairs = pair_inventory_to_descriptions(segments["inventory_runs"], segments["description_runs"])
    print("\nNaive pairs:")
    for i, p in enumerate(pairs, 1):
        print(f"Inventory {i}: pages {p['inventory']['pages']} -> descriptions {[d['pages'] for d in p['descriptions']]}")
