# fine_tune_metes_bounds.py
from openai import OpenAI
import os, time

client = OpenAI()  # uses OPENAI_API_KEY env var

TRAIN_PATH = "training.jsonl"        # your path
VAL_PATH    = "validation.jsonl"     # optional

def upload(path):
    with open(path, "rb") as f:
        file = client.files.create(file=f, purpose="fine-tune")
    return file.id

print("Uploading files...")
train_file_id = upload(TRAIN_PATH)
val_file_id = upload(VAL_PATH) if os.path.exists(VAL_PATH) else None
print("train_file_id:", train_file_id)
print("val_file_id:", val_file_id)

BASE_MODEL = "gpt-4o-mini-2024-07-18"  # example fine-tuneable model

print("Creating fine-tune job...")
job = client.fine_tuning.jobs.create(
    training_file=train_file_id,
    validation_file=val_file_id,
    model=BASE_MODEL,
    suffix="metes-bounds-parser"
)
job_id = job.id
print("job_id:", job_id)

# (optional) poll status
while True:
    j = client.fine_tuning.jobs.retrieve(job_id)
    print("status:", j.status)
    if j.status in ("succeeded", "failed", "cancelled"):
        break
    time.sleep(10)

# (optional) show training log events
events = client.fine_tuning.jobs.list_events(job_id)
for e in reversed(events.data):
    print(e.message)

# get your new model id
j = client.fine_tuning.jobs.retrieve(job_id)
ft_model = j.fine_tuned_model
print("fine-tuned model:", ft_model)
