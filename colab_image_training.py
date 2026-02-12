# COLAB CELLS

#!!! CELL 1.1 Mount Drive & set paths ##
########################################
from google.colab import drive
drive.mount('/content/drive')

BASE = '/content/drive/MyDrive/deeds'
PAGES_DIR = f'{BASE}/data/pages'
INV_DIR   = f'{BASE}/data/positives/inventory'
DESC_DIR  = f'{BASE}/data/positives/property_description'
CONV_DIR  = f'{BASE}/data/positives/conveyance'  # optional

#!!! CELL 2) Build the training CSV (does OCR once)##
#####################################################
import os, csv, glob
from pathlib import Path
from PIL import Image
import pytesseract

inv_names  = set(Path(p).name for p in glob.glob(f"{INV_DIR}/*.png"))
desc_names = set(Path(p).name for p in glob.glob(f"{DESC_DIR}/*.png"))
conv_names = set(Path(p).name for p in glob.glob(f"{CONV_DIR}/*.png")) if os.path.isdir(CONV_DIR) else set()

def label_for(name):
    if name in inv_names:  return "inventory"
    if name in desc_names: return "property_description"
    if name in conv_names: return "conveyance"
    return "other"

rows = []
for doc_dir in sorted(Path(PAGES_DIR).iterdir()):
    if not doc_dir.is_dir(): 
        continue
    for img_path in sorted(doc_dir.glob("*.png")):
        name = img_path.name
        label = label_for(name)
        text = pytesseract.image_to_string(Image.open(img_path))
        rows.append({
            "doc_id": doc_dir.name,
            "page": name,
            "label": label,
            "text": text,
            "path": str(img_path)
        })

import pandas as pd
df = pd.DataFrame(rows)
out_csv = f"{BASE}/dataset.csv"
df.to_csv(out_csv, index=False)
len(df), out_csv

#!!! CELL 3) Train a simple, strong baseline (TF-IDF + Logistic Regression)#
############################################################################
import pandas as pd, joblib
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report

df = pd.read_csv(f"{BASE}/dataset.csv")
X_train, X_val, y_train, y_val = train_test_split(
    df["text"], df["label"], test_size=0.2, stratify=df["label"], random_state=42
)

pipe = Pipeline([
    ("tfidf", TfidfVectorizer(ngram_range=(1,2), max_features=50000, lowercase=True)),
    ("clf", LogisticRegression(max_iter=2000, class_weight="balanced"))
])

pipe.fit(X_train, y_train)
print(classification_report(y_val, pipe.predict(X_val)))

model_path = f"{BASE}/page_classifier.joblib"
joblib.dump(pipe, model_path)
model_path

#!!! CELL 4) Inference helper that groups descriptions into tracts#
###################################################################
import re, json, joblib
from PIL import Image
import pytesseract
from pathlib import Path

MODEL_PATH = f"{BASE}/page_classifier.joblib"
pipe = joblib.load(MODEL_PATH)

TRACT_RE = re.compile(r'\b(TRACT|PARCEL|LOT)\s*([A-Z]+|\d+|ONE|TWO|THREE|I|II|III)\b', re.I)
MNB_CUES = re.compile(r'\b(BEGINNING|COMMENCING|THENCE|COURSES AND DISTANCES|POINT OF BEGINNING|P\.O\.B\.|P\.O\.T\.)\b', re.I)

def ocr_text(p):
    return pytesseract.image_to_string(Image.open(p))

def classify_text(t):
    proba = pipe.predict_proba([t])[0]
    label = pipe.classes_[proba.argmax()]
    return label, float(proba.max())

def split_into_tracts(pages_with_text):
    tracts = []
    current = {"title": None, "pages": [], "texts": []}

    def flush():
        if current["pages"]:
            all_text = "\n".join(current["texts"])
            m = TRACT_RE.search(all_text)
            title = m.group(0) if m else current["title"] or f"TRACT {len(tracts)+1}"
            tracts.append({"tract_id": title, "pages": current["pages"][:], "text": all_text})
            current["title"] = None
            current["pages"].clear()
            current["texts"].clear()

    for (idx, t) in pages_with_text:
        if current["pages"] and (TRACT_RE.search(t) or (MNB_CUES.search(t) and len("".join(current["texts"])) > 800)):
            flush()
        if not current["pages"]:
            m = TRACT_RE.search(t)
            current["title"] = m.group(0) if m else None
        current["pages"].append(idx)
        current["texts"].append(t)

    flush()
    return tracts

def process_document(doc_path):
    doc_path = Path(doc_path)
    imgs = sorted(doc_path.glob("*.png"))
    page_info = []
    for i, p in enumerate(imgs):
        t = ocr_text(p)
        label, conf = classify_text(t)
        page_info.append({"index": i, "path": str(p), "label": label, "confidence": conf, "text": t})

    inventories  = [p for p in page_info if p["label"] == "inventory"]
    descriptions = [p for p in page_info if p["label"] == "property_description"]

    tracts = split_into_tracts([(p["index"], p["text"]) for p in descriptions])

    return {
        "doc_id": doc_path.name,
        "inventories": [{"page_index": p["index"], "path": p["path"]} for p in inventories],
        "descriptions": tracts
    }

#!!! CELL FINAL#
################
result = process_document(f"{PAGES_DIR}/doc_001")
print(json.dumps(result, indent=2)[:2000])  # preview