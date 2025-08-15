import os
import re
import json
from collections import defaultdict
import fitz
import boto3
from openai import OpenAI

from schema_function import EXTRACT_METES_BOUNDS_SCHEMA, SYSTEM_PROMPT_LINES

def format_bearing(bearing_str):
    # case-insensitive regex to grab dir1, deg, min, sec, dir2
    pattern = re.compile(
        r"^\s*"
        r"(?P<dir1>north|south)\s+"              # N or S word
        r"(?P<deg>\d+)\s*(?:°|degrees?)\s+"
        r"(?P<min>\d+)\s*(?:'|minutes?)\s+"
        r"(?P<sec>\d+)\s*(?:\"|seconds?)\s+"
        r"(?P<dir2>east|west)\s*"
        r"$",
        flags=re.IGNORECASE
    )
    m = pattern.match(bearing_str)
    if not m:
        # fallback: if it doesn’t match, just return the original
        return bearing_str

    # map any case-variant to its initial
    abbrev = {"north": "N", "south": "S", "east": "E", "west": "W"}
    d1 = abbrev[m.group("dir1").lower()]
    d2 = abbrev[m.group("dir2").lower()]

    deg = m.group("deg")
    minute = m.group("min")
    sec = m.group("sec")

    return f"{d1} {deg}°{minute}'{sec}\" {d2}"


def clean_deed_text(text):
    # 1) Remove repeated headers/footers
    text = re.sub(
        r"Texas Department of Transportation.*?Page\s*\d+\s*of\s*\d+",
        "",
        text,
        flags=re.IGNORECASE
    )

    # 2) Drop standalone “EXHIBIT” markers
    text = re.sub(r"\bEXHIBIT\b", "", text, flags=re.IGNORECASE)

    # 3) Remove Windows‑style file paths
    text = re.sub(
        r"[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s\\]+",
        "",
        text
    )

    # 4) Strip stray asterisks
    text = re.sub(r"\*+", "", text)

    # 5) Newline before each “THENCE”
    text = re.sub(r"(?i)\bTHENCE\b", r"\nTHENCE", text)

    # 5.1) Break out each bearing‑distance clause onto its own line
    pattern = re.compile(r"""
        [\:\.,]\s*                                  # leading colon, comma or period + spaces
        (                                           # capture the bearing+distance clause
          (?:N|S|E|W|North|South|East|West)
          \s*(?:[0-9]|[1-8]\d|90)\s*(?:°|degrees?)\s*
          (?:[0-5]?\d)\s*(?:'|minutes?)\s*
          (?:[0-5]?\d)\s*(?:"|seconds?)\s*
          (?:E|W|East|West)?
          (?:                                       # optional distance clause
            \s*,?\s*a\s*distance\s*of\s*,?\s*
            \d+(?:\.\d+)?\s*(?:feet|chains)
          )?
        )
        (?=                                         # lookahead for punctuation or next bearing
            [\:\.,\s]*(?:N|S|E|W|North|South|East|West)\b
          | [\:\.,]
        )
        """,
        flags=re.IGNORECASE | re.VERBOSE
    )
    text = pattern.sub(lambda m: f", {m.group(1)},\n", text)

    # 6) Replace any leading punctuation+space at start‐of‐line with “THENCE ”,
    #    but skip if it's a coordinate that starts “N:” or “E:” etc.
    text = re.sub(
        r'(?m)^[\:\.,]\s*(?!(?:[NSEW]:))',   # NEGATIVE lookahead for N: / E: coordinate
        'THENCE ',
        text
    )

    # 7) Collapse multiple spaces
    text = re.sub(r"\s{2,}", " ", text).strip()

    # 8) Insert newline before any comma/colon/period that precedes a bearing,
    #    but skip when that bearing is actually a coordinate (N:123,…)
    text = re.sub(
        r'([,.:])\s*'                                     
        r'(?=(?:N(?!:)|S(?!:)|E(?!:)|W(?!:)|North(?!:)|South(?!:)|East(?!:)|West(?!:))\b)',
        r'\1\n',
        text,
        flags=re.IGNORECASE
    )

    # 9) Prefix any line now starting with a compass direction with “THENCE ”,
    #    again skipping coordinate pairs
    text = re.sub(
        r'(?m)^(?=\s*(?:N(?!:)|S(?!:)|E(?!:)|W(?!:)|North(?!:)|South(?!:)|East(?!:)|West(?!:))\b)',
        'THENCE ',
        text
    )

    text = re.sub(r"\s{2,}", " ", text).strip()

    return text


def words_to_lines(blocks, y_tol=0.005):
    rows = defaultdict(list)
    # group words whose Top coordinate is within y_tol of each other
    for b in blocks:
        if b["BlockType"]!="WORD": continue
        top = b["Geometry"]["BoundingBox"]["Top"]
        # find an existing row within tolerance, or start a new one
        key = next((k for k in rows if abs(k - top) < y_tol), None)
        rows[key or top].append(b)
    # sort rows top→bottom, then words left→right
    lines = []
    for top in sorted(rows):
        words = sorted(rows[top], key=lambda w: w["Geometry"]["BoundingBox"]["Left"])
        lines.append(" ".join(w["Text"] for w in words))
    return lines


# 1) Rasterize PDF to images
def pdf_to_images(path, dpi=300):
    doc = fitz.open(path)
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    images = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat, alpha=False)
        images.append(pix.tobytes("png"))
    return images

metes = os.path.join(os.path.dirname(__file__), "1194_995_TrucksAndStuffs/NM-ED-00022.00081 .pdf")

open_client = OpenAI(api_key="")

# Initialize AWS Textract client
textract = boto3.client(
    "textract",
    aws_access_key_id="",
    aws_secret_access_key="",
    region_name="us-east-2"
)

def process_page(cleaned_pages, start_idx):
    """
    Stitch together cleaned_pages[start_idx] plus as many subsequent pages
    as needed up through the first semicolon.  Return a tuple:
      (stitched_text, segments, last_idx_consumed)
    If we find a semicolon on a later page, we only mark start_idx as consumed,
    leaving the remainder of that later page in cleaned_pages for the next pass.
    """
    combined = cleaned_pages[start_idx].strip()
    idx = start_idx
    partial = False  # did we cut in the middle of a page?

    # Keep pulling until we see a semicolon or run out of pages
    while not combined.endswith(";") and idx + 1 < len(cleaned_pages):
        idx += 1
        next_txt = cleaned_pages[idx]
        if ";" in next_txt:
            # consume up through that semicolon
            pos = next_txt.find(";")
            combined += " " + next_txt[: pos + 1].strip()
            # leave the remainder for next time
            cleaned_pages[idx] = next_txt[pos + 1 :].strip()
            partial = True
            break
        else:
            # no semicolon here, consume the whole page
            combined += " " + next_txt.strip()

    # if we exhausted pages without finding a semicolon, idx will be last page
    # if combined now ends in ';' but partial==True, we've only consumed start_idx
    # otherwise we consumed through idx
    last_consumed = start_idx if partial else idx

    # call the LLM on our stitched chunk
    response = open_client.chat.completions.create(
        model="gpt-4-0613",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_LINES},
            {"role":   "user", "content": combined}
        ],
        functions     = [EXTRACT_METES_BOUNDS_SCHEMA],
        function_call = {"name": "extract_metes_bounds"},
        temperature   = 0
    )

    args = json.loads(response.choices[0].message.function_call.arguments)
    segments = args.get("segments", [])

    # normalize bearings
    for s in segments:
        if s.get("bearing"):
            s["bearing"] = format_bearing(s["bearing"])

    return combined, segments, last_consumed


def main():
    # rasterize / OCR / clean all pages first
    pages = pdf_to_images(metes)
    cleaned_pages = []
    for png in pages:
        resp  = textract.detect_document_text(Document={"Bytes": png})
        lines = words_to_lines(resp["Blocks"])
        raw   = " ".join(lines)
        cleaned_pages.append(clean_deed_text(raw))

    all_segments = []
    i = 0
    while i < len(cleaned_pages):
        stitched_text, segs, consumed = process_page(cleaned_pages, i)
        all_segments.extend(segs)
        # if we only consumed the start page, move one ahead;
        # if we consumed through a full page-run, skip those
        i = consumed + 1

    # now build the prompt from whatever remains
    full_prompt = "\n".join(cleaned_pages).strip()

    # write out JSON
    output = {
        "prompt":     full_prompt,
        "completion": json.dumps({"segments": all_segments}, ensure_ascii=False)
    }
    with open("output.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Extracted {len(all_segments)} segments across {len(pages)} pages.")


    for seg in all_segments:
        print(json.dumps(seg, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()