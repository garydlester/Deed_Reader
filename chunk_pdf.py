import os
import re
import json
from collections import defaultdict
import fitz
import boto3
from openai import OpenAI

from schema_function import EXTRACT_METES_BOUNDS_SCHEMA, SYSTEM_PROMPT_LINES

def format_bearing(bearing_str: str) -> str:
    """
    Normalizes many OCR/format variants into:  N 32°44'45" W
    Fixes:
      - N 32"44'35" W      -> N 32°44'35" W
      - N 32'44'45" W      -> N 32°44'45" W
      - N41'44'55"W        -> N 41°44'55" W
      - N34'45'W           -> N 34°45'00" W
      - S. 15'29'56' E     -> S 15°29'56" E
      - S. 74:34'W, S 74:34 W -> S 74°34'00" W
      - SOUTH 39'01'15" CAST -> S 39°01'15" E
      - Worded units incl. typos (DEUREES/MINUTES/SECONDS)
    Enforces bearings <= 90°00'00" by “defusing” concatenated degree digits:
      e.g. N 145°0'28" W -> N 14°50'28" W
    """
    if not bearing_str or not bearing_str.strip():
        return bearing_str

    s = bearing_str

    # Normalize quotes to ASCII and strip trailing punctuation
    s = (s.replace("’", "'").replace("′", "'")
           .replace("“", '"').replace("”", '"'))
    s = re.sub(r"[\s,;:\.\)\]]+$", "", s)

    # OCR direction words & common typo CAST -> E ; full words -> letters
    def dir_fix(m):
        w = m.group(0).upper()
        return {"NORTH":"N","SOUTH":"S","EAST":"E","WEST":"W","CAST":"E"}.get(w, m.group(0))
    s = re.sub(r"\b(NORTH|SOUTH|EAST|WEST|CAST)\b", dir_fix, s, flags=re.IGNORECASE)

    # Worded units (with OCR typos) -> symbols
    s = re.sub(r"\b(\d+)\s*(?:deg(?:ree|rees)?|degre?s|deqrees|deurees)\b", r"\1°", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(\d+)\s*(?:minute|minutes|min(?:ute)?s?)\b", r"\1'", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(\d+)\s*(?:second|seconds|sec(?:ond)?s?)\b", r'\1"', s, flags=re.IGNORECASE)

    # Handle colon styles: N. 41:44:55 W  → N 41°44'55" W
    s = re.sub(r"^\s*([NnSs])\.?\s*(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\s*([EeWw])\.?\s*$",
               lambda m: f"{m.group(1).upper()} {m.group(2)}°{m.group(3)}'{m.group(4)}\" {m.group(5).upper()}",
               s)
    # Colon with no seconds: S. 74:34'W / S 74:34 W → S 74°34'00" W
    s = re.sub(r"^\s*([NnSs])\.?\s*(\d+)\s*:\s*(\d+)\s*'?[\s]*([EeWw])\.?\s*$",
               lambda m: f"{m.group(1).upper()} {m.group(2)}°{m.group(3)}'00\" {m.group(4).upper()}",
               s)

    # Apostrophes everywhere: S. 15'29'56' E → S 15°29'56" E
    s = re.sub(r"^\s*([NnSs])\.?\s*(\d+)'(\d+)'(\d+)'?\s*([EeWw])\.?\s*$",
               lambda m: f"{m.group(1).upper()} {m.group(2)}°{m.group(3)}'{m.group(4)}\" {m.group(5).upper()}",
               s)

    # Deg+Min only with apostrophes: N34'45'W / N 34'45' W → N 34°45'00" W
    s = re.sub(r"^\s*([NnSs])\s*(\d+)'(\d+)'\s*([EeWw])\s*$",
               lambda m: f"{m.group(1).upper()} {m.group(2)}°{m.group(3)}'00\" {m.group(4).upper()}",
               s)

    # Compact no-space: N41'44'55"W → N 41°44'55" W
    s = re.sub(r"^\s*([NnSs])\s*(\d+)'(\d+)'(\d+)(?:\")?\s*([EeWw])\s*$",
               lambda m: f"{m.group(1).upper()} {m.group(2)}°{m.group(3)}'{m.group(4)}\" {m.group(5).upper()}",
               s)

    # Misplaced " used as degrees, or ' used as degrees (before a proper pattern)
    s = re.sub(r'(\d+)"(?=\s*\d+\'\d+"?\s*[EW])', r"\1°", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d+)'(?=\s*\d+'\d+\"?\s*[EW])", r"\1°", s, flags=re.IGNORECASE)

    # Ensure a space before trailing E/W if it's glued: …55"W → …55" W
    s = re.sub(r'(\d)"\s*([EW])\s*$', r'\1" \2', s, flags=re.IGNORECASE)

    # Final parse (accept words or symbols, optional minutes/seconds)
    rx = re.compile(r"""
        ^\s*
        (?P<dir1>N|S|north|south)\s*
        (?P<deg>\d+(?:\.\d+)?)\s*(?:°|degrees?)?
        (?:\s*(?P<min>\d+(?:\.\d+)?)\s*(?:'|minutes?)?)?
        (?:\s*(?P<sec>\d+(?:\.\d+)?)\s*(?:"|seconds?)?)?
        \s*(?P<dir2>E|W|east|west)
        \s*$
    """, re.IGNORECASE | re.VERBOSE)

    m = rx.match(s)
    if not m:
        return s.strip()

    def abbr(t):
        t = t.lower()
        return "N" if t.startswith("n") else "S" if t.startswith("s") else "E" if t.startswith("e") else "W"
    d1 = abbr(m.group("dir1"))
    d2 = abbr(m.group("dir2"))

    def to_int(v):
        if not v: return 0
        try: return int(float(v))
        except: 
            mm = re.search(r"\d+", v)
            return int(mm.group(0)) if mm else 0

    deg = to_int(m.group("deg"))
    minu = to_int(m.group("min") or "0")
    sec  = to_int(m.group("sec") or "0")

    # Enforce ≤90°: defuse concatenated degree digits if needed
    if deg > 90:
        ds = str(deg)
        if len(ds) >= 2:
            last = int(ds[-1])
            deg = int(ds[:-1])
            minu += last * 10

    # Normalize overflow
    if sec >= 60:
        minu += sec // 60
        sec  %= 60
    if minu >= 60:
        deg  += minu // 60
        minu %= 60
    if deg > 90:
        deg, minu, sec = 90, 0, 0

    return f'{d1} {deg}°{minu:02d}\'{sec:02d}" {d2}'

# ------------------ Deed cleaning ------------------

_LATLON_DMS = re.compile(
    r"""\b
        (?:N|S)\s*\d{1,3}°\s*\d{1,2}'\s*\d{1,2}(?:\.\d+)?"
        \s*
        (?:E|W)\s*\d{1,3}°\s*\d{1,2}'\s*\d{1,2}(?:\.\d+)?"
    \b""", re.IGNORECASE | re.VERBOSE)

_LATLON_DEC = re.compile(
    r"""\b
        (?:N|S)\s*-?\d{1,3}(?:\.\d+)?\s*°?
        \s*
        (?:E|W)\s*-?\d{1,3}(?:\.\d+)?\s*°?
    \b""", re.IGNORECASE | re.VERBOSE)


def _shield_latlon(text: str) -> tuple[str, dict[int, str]]:
    """Replace lat/long pairs with placeholders to avoid being split or rewritten."""
    repl = {}
    idx = 0

    def subfn(pattern, t):
        nonlocal idx
        def _r(m):
            nonlocal idx
            tag = f"__LL_PLACEHOLDER_{idx}__"
            repl[idx] = m.group(0)
            idx += 1
            return tag
        return pattern.sub(_r, t)

    text = subfn(_LATLON_DMS, text)
    text = subfn(_LATLON_DEC, text)
    return text, repl


def _unshield_latlon(text: str, repl: dict[int, str]) -> str:
    for k, v in repl.items():
        text = text.replace(f"__LL_PLACEHOLDER_{k}__", v)
    return text


def clean_deed_text(text: str) -> str:
    """
    Cleans raw deed text:
      - remove headers/footers, EXHIBIT, Windows paths, stray asterisks
      - insert newlines before THENCE
      - split bearing/distance clauses onto separate lines
      - avoid touching coordinate pairs like N: 12345, E: 67890 and lat/long pairs
      - collapse whitespace
    """
    if not text:
        return text

    # Shield lat/long pairs so we don't split inside them
    text, saved_coords = _shield_latlon(text)

    # 1) Remove repeated headers/footers “Texas Department of Transportation ... Page X of Y”
    text = re.sub(r"Texas Department of Transportation.*?Page\s*\d+\s*of\s*\d+",
                  "", text, flags=re.IGNORECASE | re.DOTALL)

    # 2) Drop standalone “EXHIBIT”
    text = re.sub(r"\bEXHIBIT\b", "", text, flags=re.IGNORECASE)

    # 3) Remove Windows paths like K:\PROJ\...
    text = re.sub(r"[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s\\]+", "", text)

    # 4) Strip stray asterisks
    text = re.sub(r"\*+", "", text)

    # Normalize quotes to ASCII
    text = (text.replace("’", "'").replace("′", "'")
                .replace("“", '"').replace("”", '"'))

    # 5) Newline before each THENCE (case-insensitive)
    text = re.sub(r"(?i)\bTHENCE\b", "\nTHENCE", text)

    # 5.1) Break bearing-distance clauses onto their own line.
    #      Accept symbols or words; allow colon styles; optional trailing distance clause.
    bearing_clause = re.compile(r"""
        [\:\.,]\s*                                  # leading punctuation + spaces
        (                                           # capture the clause
          (?:N|S|E|W|North|South|East|West)\.?\s*
          (?:\d{1,2}|[1-8]\d|90)\s*(?:°|degrees?)?\s*
          (?:
              (?:[0-5]?\d)\s*(?:'|minutes?)\s*
              (?:
                 (?:[0-5]?\d)\s*(?:"|seconds?)      # with seconds
              )?
            |
              :\s*[0-5]?\d(?:\s*:\s*[0-5]?\d)?      # colon styles: D:MM or D:MM:SS
          )\s*
          (?:E|W|East|West)?
          (?:\s*,?\s*a\s*distance\s*of\s*,?\s*\d+(?:\.\d+)?\s*(?:feet|chains))?
        )
        (?=                                         # next delimiter or next bearing
            [\:\.,\s]*(?:N|S|E|W|North|South|East|West)\b
          | [\:\.,]
        )
    """, re.IGNORECASE | re.VERBOSE)
    text = bearing_clause.sub(lambda m: f", {m.group(1)},\n", text)

    # 6) Replace any leading punctuation at start-of-line with "THENCE ",
    #    skipping coordinates like "N:" / "E:" northings/eastings.
    text = re.sub(r'(?m)^[\:\.,]\s*(?!(?:[NSEW]:))', 'THENCE ', text)

    # 7) Insert newline before punctuation that precedes a bearing word/letter,
    #    but skip when it's a coordinate (N:123…)
    text = re.sub(
        r'([,.:])\s*(?=(?:N(?!:)|S(?!:)|E(?!:)|W(?!:)|North(?!:)|South(?!:)|East(?!:)|West(?!:))\b)',
        r'\1\n', text, flags=re.IGNORECASE
    )

    # 8) Ensure lines that start with a compass direction begin with "THENCE "
    text = re.sub(
        r'(?m)^(?=\s*(?:N(?!:)|S(?!:)|E(?!:)|W(?!:)|North(?!:)|South(?!:)|East(?!:)|West(?!:))\b)',
        'THENCE ', text
    )

    # 9) Collapse whitespace
    text = re.sub(r"\s{2,}", " ", text).strip()

    # Unshield lat/long pairs
    text = _unshield_latlon(text, saved_coords)
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

metes = os.path.join(os.path.dirname(__file__), r"Title\UPSEG2 - West Texas Land & Water LLC\West Texas Land & Water LLC\2024-10288_ROW_SEC 23.pdf")

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