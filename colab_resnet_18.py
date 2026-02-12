#################################################################################################
# 1) Install & imports (Colab)
# colab command line commands
# !pip -q install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
#!pip -q install pandas scikit-learn pillow tqdm

#################################################################################################
# 2) Config + data load

import os, json, random
import pandas as pd
from PIL import Image
from tqdm import tqdm

import torch
from torch import nn
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from torchvision import transforms, models

# ==== EDIT THESE ====
CSV_PATH = "/content/dataset.csv"          # your CSV with image paths + labels
IMAGE_COL = "image_path"                   # column with absolute or relative PNG paths
LABEL_COL = "label"                        # values: "inventory", "property_description", "other"
SPLIT_COL = "split"                        # "train" / "val" (if you already created it)
OUT_DIR   = "/content/deed_model_out"      # where to save model + label map
NUM_EPOCHS = 5
BATCH_SIZE = 16
LR = 2e-4
NUM_WORKERS = 2
# ================

os.makedirs(OUT_DIR, exist_ok=True)

df = pd.read_csv(CSV_PATH)
assert IMAGE_COL in df.columns and LABEL_COL in df.columns

# If you already have splits, use them; else quick stratified split here:
if SPLIT_COL not in df.columns:
    df = df.sample(frac=1.0, random_state=42).reset_index(drop=True)
    df["split"] = "train"
    df.loc[df.index[-max(1, int(0.15*len(df))):], "split"] = "val"
else:
    # Normalize split values
    df[SPLIT_COL] = df[SPLIT_COL].str.lower()

train_df = df[df[SPLIT_COL] == "train"].copy()
val_df   = df[df[SPLIT_COL] == "val"].copy()
print(f"train: {len(train_df)}   val: {len(val_df)}")

# Label mapping
labels = sorted(df[LABEL_COL].unique())
label2idx = {lab:i for i,lab in enumerate(labels)}
idx2label = {i:lab for lab,i in label2idx.items()}
with open(os.path.join(OUT_DIR, "labels.json"), "w") as f:
    json.dump({"label2idx":label2idx, "idx2label":idx2label}, f, indent=2)
print(label2idx)

#################################################################################################
# 3) Dataset & transforms

IMNET_MEAN = [0.485, 0.456, 0.406]
IMNET_STD  = [0.229, 0.224, 0.225]

train_tf = transforms.Compose([
    transforms.Resize((640, 640)),
    transforms.RandomHorizontalFlip(p=0.0),  # legal docs—keep deterministic if you prefer
    transforms.ToTensor(),
    transforms.Normalize(IMNET_MEAN, IMNET_STD),
])

val_tf = transforms.Compose([
    transforms.Resize((640, 640)),
    transforms.ToTensor(),
    transforms.Normalize(IMNET_MEAN, IMNET_STD),
])

class DeedDataset(Dataset):
    def __init__(self, frame, image_col, label_col, tfm):
        self.f = frame.reset_index(drop=True)
        self.image_col = image_col
        self.label_col = label_col
        self.tfm = tfm

    def __len__(self): return len(self.f)

    def __getitem__(self, i):
        row = self.f.iloc[i]
        p = row[self.image_col]
        # If paths in CSV are relative, make them abs here as needed:
        img = Image.open(p).convert("RGB")
        x = self.tfm(img)
        y = label2idx[row[self.label_col]]
        return x, y, p  # include path for debugging

train_ds = DeedDataset(train_df, IMAGE_COL, LABEL_COL, train_tf)
val_ds   = DeedDataset(val_df,   IMAGE_COL, LABEL_COL, val_tf)

# Handle class imbalance
class_counts = train_df[LABEL_COL].value_counts().reindex(labels).fillna(0).values
weights = 1.0 / torch.tensor(class_counts, dtype=torch.float).clamp(min=1)
sample_weights = [weights[label2idx[y]] for y in train_df[LABEL_COL]]
sampler = WeightedRandomSampler(sample_weights, num_samples=len(sample_weights), replacement=True)

train_dl = DataLoader(train_ds, batch_size=BATCH_SIZE, sampler=sampler, num_workers=NUM_WORKERS, pin_memory=True)
val_dl   = DataLoader(val_ds,   batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS, pin_memory=True)

#################################################################################################
# 4) Model, training loop, evaluation

device = "cuda" if torch.cuda.is_available() else "cpu"

model = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1)
model.fc = nn.Linear(model.fc.in_features, len(labels))
model = model.to(device)

criterion = nn.CrossEntropyLoss()
optimizer = torch.optim.AdamW(model.parameters(), lr=LR)

def run_epoch(dl, train=True):
    model.train(train)
    total, correct, total_loss = 0, 0, 0.0
    with torch.set_grad_enabled(train):
        for x,y,_ in tqdm(dl, disable=False):
            x,y = x.to(device), y.to(device)
            if train: optimizer.zero_grad()
            logits = model(x)
            loss = criterion(logits, y)
            if train:
                loss.backward()
                optimizer.step()
            total_loss += loss.item() * x.size(0)
            pred = logits.argmax(1)
            correct += (pred == y).sum().item()
            total += x.size(0)
    return total_loss/total, correct/total

best_val = 0.0
for epoch in range(1, NUM_EPOCHS+1):
    tr_loss, tr_acc = run_epoch(train_dl, train=True)
    va_loss, va_acc = run_epoch(val_dl,   train=False)
    print(f"epoch {epoch}: train loss {tr_loss:.4f} acc {tr_acc:.3f} | val loss {va_loss:.4f} acc {va_acc:.3f}")
    if va_acc > best_val:
        best_val = va_acc
        torch.save(model.state_dict(), os.path.join(OUT_DIR, "resnet18_deed.pt"))
        print("  ✔ saved best model")

#################################################################################################
# 5) Quick metrics (per-class report)

from sklearn.metrics import classification_report

# load best and evaluate for a clean report
model.load_state_dict(torch.load(os.path.join(OUT_DIR, "resnet18_deed.pt"), map_location=device))
model.eval()

y_true, y_pred = [], []
with torch.no_grad():
    for x,y,_ in val_dl:
        x = x.to(device)
        logits = model(x)
        y_true.extend(y.tolist())
        y_pred.extend(logits.argmax(1).cpu().tolist())

print(classification_report(
    y_true, y_pred, target_names=[idx2label[i] for i in range(len(labels))], digits=3
))

#################################################################################################
# 6) Inference helper (single or batch; grouped per deed)

import torch.nn.functional as F
from collections import defaultdict

infer_tf = val_tf  # same as validation
model.eval()

def predict_paths(image_paths, topk=1):
    out = []
    with torch.no_grad():
        for p in image_paths:
            img = Image.open(p).convert("RGB")
            x = infer_tf(img).unsqueeze(0).to(device)
            logits = model(x)
            probs = F.softmax(logits, dim=1).squeeze(0).cpu().numpy()
            top = probs.argmax()
            out.append({
                "path": p,
                "label": idx2label[top],
                "prob": float(probs[top]),
                "probs": {idx2label[i]: float(probs[i]) for i in range(len(labels))}
            })
    return out

def group_by_document(preds):
    """
    Assumes paths look like .../data/<DOC_ID>/pages/0001.png or similar.
    Edit the doc-id extraction if your layout differs.
    """
    def doc_id_from_path(p):
        parts = os.path.normpath(p).split(os.sep)
        # find a folder name one level above the image file
        # tweak as needed for your structure
        if "pages" in parts:
            i = parts.index("pages")
            return parts[i-1] if i > 0 else "unknown"
        return parts[-2]  # fallback
    grouped = defaultdict(lambda: {"inventory": [], "property_description": [], "other": []})
    for r in preds:
        did = doc_id_from_path(r["path"])
        grouped[did][r["label"]].append(r)
    return grouped

# Example:
# sample_paths = val_df[IMAGE_COL].sample(8, random_state=0).tolist()
# preds = predict_paths(sample_paths)
# grouped = group_by_document(preds)
# grouped  # inventories + property_descriptions per doc

#################################################################################################
#  7) Save a ready-to-load “package”

torch.save(model.state_dict(), os.path.join(OUT_DIR, "resnet18_deed.pt"))
with open(os.path.join(OUT_DIR, "labels.json"), "w") as f:
    json.dump({"label2idx":label2idx, "idx2label":idx2label}, f, indent=2)
print(f"saved to {OUT_DIR}")