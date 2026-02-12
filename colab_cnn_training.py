###################################################################
# Cell A — Load the trained CNN (ResNet) for page classification

# CNN inference: load model + transforms (drop-in)
import os, json, glob
import torch
import torch.nn as nn
import numpy as np
import pandas as pd
from PIL import Image
from torchvision import transforms, models
from torch.utils.data import DataLoader, Dataset

# --- EDIT THESE 2 PATHS to your saved model + class map (optional) ---
MODEL_PATH = "/content/drive/MyDrive/deeds/outputs/models/resnet18_pages.pt"
CLASS_TO_IDX_JSON = "/content/drive/MyDrive/deeds/outputs/models/class_to_idx.json"  # optional

# If you trained with classes ["other","inventory","property_description"], keep this order.
# If you saved a class_to_idx mapping, we’ll use it to be safe.
DEFAULT_CLASS_ORDER = ["other", "inventory", "property_description"]

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Standard ImageNet normalization (match what you used in training)
IMG_SIZE = 512
cnn_transform = transforms.Compose([
    transforms.Resize(IMG_SIZE),
    transforms.CenterCrop(IMG_SIZE),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485,0.456,0.406], std=[0.229,0.224,0.225]),
])

def load_class_order():
    if os.path.isfile(CLASS_TO_IDX_JSON):
        with open(CLASS_TO_IDX_JSON, "r") as f:
            c2i = json.load(f)  # e.g. {"other":0,"inventory":1,"property_description":2}
        # invert to get order by index
        inv = {idx:cls for cls, idx in c2i.items()}
        order = [inv[i] for i in range(len(inv))]
        return order
    return DEFAULT_CLASS_ORDER

CLASS_ORDER = load_class_order()
assert "inventory" in CLASS_ORDER and "property_description" in CLASS_ORDER, CLASS_ORDER
INV_IDX  = CLASS_ORDER.index("inventory")
DESC_IDX = CLASS_ORDER.index("property_description")

def load_cnn_model(model_path):
    model = models.resnet18(weights=None)
    model.fc = nn.Linear(model.fc.in_features, len(CLASS_ORDER))
    sd = torch.load(model_path, map_location="cpu")
    # Allow for 'model' or plain state dict
    if isinstance(sd, dict) and "state_dict" in sd:
        sd = sd["state_dict"]
        # strip possible "module." prefixes
        sd = {k.replace("module.", "").replace("model.", ""): v for k,v in sd.items()}
    model.load_state_dict(sd, strict=True)
    model.to(device).eval()
    return model

cnn_model = load_cnn_model(MODEL_PATH)
print("Loaded CNN with classes:", CLASS_ORDER, "on", device)

########################################################################
# Cell B — Predict per page with the CNN and return the SAME structures

# Same helpers your OCR pipeline used
def extract_page_number_from_name(path:str):
    import re, os
    base = os.path.basename(path)
    nums = re.findall(r"(?<!\d)(\d{3,5})(?!\d)", base)
    if not nums:
        return None
    try:
        return int(nums[0])
    except:
        return None

def contiguous_runs(mask_series, page_series):
    runs = []
    start = None
    pages_acc = []
    prev = None
    for flag, pg in zip(mask_series.tolist(), page_series.tolist()):
        if flag:
            if start is None:
                start = pg
                pages_acc = [pg]
            else:
                # if page numbers present, accept +1 as contiguous; otherwise just append
                if (prev is not None) and (pg is not None) and (pg == prev + 1):
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

class PageFolder(Dataset):
    def __init__(self, page_paths, transform):
        self.paths = page_paths
        self.tf = transform
    def __len__(self): return len(self.paths)
    def __getitem__(self, i):
        p = self.paths[i]
        img = Image.open(p).convert("RGB")
        return self.tf(img), p

@torch.no_grad()
def predict_doc_pixels(doc_dir, model, prob_threshold=0.5, batch_size=16):
    """
    CNN (pixels) backend: returns df_pred and segments like the OCR version.
    df_pred columns: doc_id, page_path, page_index, inv_prob, desc_prob, pred_inventory, pred_property_description
    """
    doc_id = os.path.basename(doc_dir.rstrip("/"))
    pages = sorted([p for p in glob.glob(os.path.join(doc_dir, "*.png")) if os.path.isfile(p)])
    if not pages:
        raise ValueError(f"No page images found in {doc_dir}")

    ds = PageFolder(pages, cnn_transform)
    dl = DataLoader(ds, batch_size=batch_size, shuffle=False, num_workers=2, pin_memory=torch.cuda.is_available())

    all_paths = []
    all_probs = []

    model.eval()
    for xb, batch_paths in dl:
        xb = xb.to(device, non_blocking=True)
        logits = model(xb)
        probs = torch.softmax(logits, dim=1).cpu().numpy()  # shape [B, C]
        all_probs.append(probs)
        all_paths.extend(batch_paths)

    probs = np.vstack(all_probs)
    inv_prob  = probs[:, INV_IDX]
    desc_prob = probs[:, DESC_IDX]
    inv_y  = (inv_prob  >= prob_threshold).astype(int)
    desc_y = (desc_prob >= prob_threshold).astype(int)

    pnums = [extract_page_number_from_name(p) for p in all_paths]

    df_pred = pd.DataFrame({
        "doc_id": doc_id,
        "page_path": all_paths,
        "page_index": pnums,
        "inv_prob": inv_prob,
        "desc_prob": desc_prob,
        "pred_inventory": inv_y,
        "pred_property_description": desc_y,
    }).sort_values(["page_index","page_path"]).reset_index(drop=True)

    inv_runs  = contiguous_runs(df_pred["pred_inventory"], df_pred["page_index"])
    desc_runs = contiguous_runs(df_pred["pred_property_description"], df_pred["page_index"])
    segments = {
        "doc_id": doc_id,
        "inventory_runs": inv_runs,
        "description_runs": desc_runs,
    }
    return df_pred, segments

# Quick smoke test on first doc folder (same as OCR cell)
if 'DATA_ROOT' in globals():
    doc_dirs = sorted([d for d in glob.glob(os.path.join(DATA_ROOT, "doc_*")) if os.path.isdir(d)])
    if doc_dirs:
        sample_doc = doc_dirs[0]
        print("CNN predicting on:", os.path.basename(sample_doc))
        df_cnn, seg_cnn = predict_doc_pixels(sample_doc, cnn_model, prob_threshold=0.5)
        display(df_cnn.head(10))
        print("\nSegments (page runs):", seg_cnn)

######################################################################
# Cell C — Optional: one unified entry that switches backends

def predict_doc_unified(doc_dir, backend="ocr", prob_threshold=0.5):
    """
    backend: "ocr" or "cnn"
    """
    if backend == "ocr":
        # expects inv_pipe, desc_pipe, ocr_image, extract_page_number_from_name to be defined (your earlier cells)
        import numpy as np, pandas as pd, glob, os
        pages = sorted(glob.glob(os.path.join(doc_dir, "*.png")))
        if not pages:
            raise ValueError(f"No page images found in {doc_dir}")
        texts  = [ocr_image(p) for p in pages]
        pnums  = [extract_page_number_from_name(p) for p in pages]
        X      = np.array(texts)
        inv_p  = inv_pipe.predict_proba(X)[:,1]
        desc_p = desc_pipe.predict_proba(X)[:,1]
        inv_y  = (inv_p  >= prob_threshold).astype(int)
        desc_y = (desc_p >= prob_threshold).astype(int)
        df = pd.DataFrame({
            "doc_id": os.path.basename(doc_dir.rstrip("/")),
            "page_path": pages,
            "page_index": pnums,
            "inv_prob": inv_p,
            "desc_prob": desc_p,
            "pred_inventory": inv_y,
            "pred_property_description": desc_y,
        }).sort_values(["page_index","page_path"]).reset_index(drop=True)
        inv_runs  = contiguous_runs(df["pred_inventory"], df["page_index"])
        desc_runs = contiguous_runs(df["pred_property_description"], df["page_index"])
        return df, {"doc_id": os.path.basename(doc_dir.rstrip("/")),
                    "inventory_runs": inv_runs, "description_runs": desc_runs}

    elif backend == "cnn":
        return predict_doc_pixels(doc_dir, cnn_model, prob_threshold=prob_threshold)
    else:
        raise ValueError("backend must be 'ocr' or 'cnn'")

#########################################################################################
# Cell D Ensemble Cell — blend OCR + CNN (same outputs as before)

# Ensemble: OCR(TFIDF) + CNN(pixels) -> same df_pred & segments
import os, glob, numpy as np, pandas as pd
import torch
from torch.utils.data import DataLoader, Dataset
from PIL import Image

# Reuse from earlier cells:
# - inv_pipe, desc_pipe  (OCR/TF-IDF models)
# - cnn_model, cnn_transform, device, CLASS_ORDER, INV_IDX, DESC_IDX
# - ocr_image(img_path)
# - extract_page_number_from_name(path)
# - contiguous_runs(mask_series, page_series)

class _PageFolder(Dataset):
    def __init__(self, page_paths, transform):
        self.paths = page_paths
        self.tf = transform
    def __len__(self): return len(self.paths)
    def __getitem__(self, i):
        im = Image.open(self.paths[i]).convert("RGB")
        return self.tf(im), self.paths[i]

@torch.no_grad()
def _cnn_probs_for_pages(page_paths, batch_size=16):
    """Return inv_prob_cnn, desc_prob_cnn aligned with page_paths."""
    ds = _PageFolder(page_paths, cnn_transform)
    dl = DataLoader(ds, batch_size=batch_size, shuffle=False,
                    num_workers=2, pin_memory=torch.cuda.is_available())
    probs_all = []
    for xb, _paths in dl:
        xb = xb.to(device, non_blocking=True)
        logits = cnn_model(xb)
        probs = torch.softmax(logits, dim=1).cpu().numpy()
        probs_all.append(probs)
    probs = np.vstack(probs_all)  # [N, C]
    inv_prob_cnn  = probs[:, INV_IDX]
    desc_prob_cnn = probs[:, DESC_IDX]
    return inv_prob_cnn, desc_prob_cnn

def predict_doc_ensemble(
    doc_dir,
    w_cnn_inv=0.5, w_cnn_desc=0.5,    # weight for CNN in the blend (0..1). 0.5 = equal blend
    prob_threshold_inv=0.5, prob_threshold_desc=0.5,
    include_debug_columns=False,
    cnn_batch_size=16
):
    """
    Returns:
      df_pred with columns:
        doc_id, page_path, page_index, inv_prob, desc_prob, pred_inventory, pred_property_description
        (+ optional debug columns if include_debug_columns=True)
      segments dict with inventory_runs / description_runs
    """
    doc_id = os.path.basename(doc_dir.rstrip("/"))
    page_paths = sorted([p for p in glob.glob(os.path.join(doc_dir, "*.png")) if os.path.isfile(p)])
    if not page_paths:
        raise ValueError(f"No page images found in {doc_dir}")

    # --- OCR/TF-IDF probabilities ---
    texts = [ocr_image(p) for p in page_paths]
    X = np.array(texts)
    inv_prob_ocr  = inv_pipe.predict_proba(X)[:, 1]
    desc_prob_ocr = desc_pipe.predict_proba(X)[:, 1]

    # --- CNN probabilities ---
    inv_prob_cnn, desc_prob_cnn = _cnn_probs_for_pages(page_paths, batch_size=cnn_batch_size)

    # --- Blend ---
    inv_prob  = (1 - w_cnn_inv)  * inv_prob_ocr  + w_cnn_inv  * inv_prob_cnn
    desc_prob = (1 - w_cnn_desc) * desc_prob_ocr + w_cnn_desc * desc_prob_cnn

    # --- Labels from blended probs (per-task thresholds allowed) ---
    pred_inventory             = (inv_prob  >= prob_threshold_inv).astype(int)
    pred_property_description  = (desc_prob >= prob_threshold_desc).astype(int)

    pnums = [extract_page_number_from_name(p) for p in page_paths]
    cols = {
        "doc_id": doc_id,
        "page_path": page_paths,
        "page_index": pnums,
        "inv_prob": inv_prob,
        "desc_prob": desc_prob,
        "pred_inventory": pred_inventory,
        "pred_property_description": pred_property_description,
    }
    if include_debug_columns:
        cols.update({
            "inv_prob_ocr": inv_prob_ocr,
            "inv_prob_cnn": inv_prob_cnn,
            "desc_prob_ocr": desc_prob_ocr,
            "desc_prob_cnn": desc_prob_cnn,
        })

    df_pred = pd.DataFrame(cols).sort_values(["page_index", "page_path"]).reset_index(drop=True)

    # --- Same segments structure as before ---
    inv_runs  = contiguous_runs(df_pred["pred_inventory"], df_pred["page_index"])
    desc_runs = contiguous_runs(df_pred["pred_property_description"], df_pred["page_index"])
    segments = {
        "doc_id": doc_id,
        "inventory_runs": inv_runs,
        "description_runs": desc_runs,
    }
    return df_pred, segments

# 🔎 Example: run ensemble on first doc folder
if 'DATA_ROOT' in globals():
    doc_dirs = sorted([d for d in glob.glob(os.path.join(DATA_ROOT, "doc_*")) if os.path.isdir(d)])
    if doc_dirs:
        sample_doc = doc_dirs[0]
        print("Ensemble predicting on:", os.path.basename(sample_doc))
        df_ens, seg_ens = predict_doc_ensemble(
            sample_doc,
            w_cnn_inv=0.5, w_cnn_desc=0.5,       # tweak these if one backend is stronger
            prob_threshold_inv=0.5, prob_threshold_desc=0.5,
            include_debug_columns=True
        )
        display(df_ens.head(10))
        print("\nSegments (page runs):", seg_ens)


