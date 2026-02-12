#!/usr/bin/env python3
import json
import sys
import argparse

# --- Your original system prompt (verbatim) ---
SYSTEM_PROMPT = """You are a land survey technician. Parse the deed text into a JSON object matching the EXTRACT_METES_BOUNDS_SCHEMA.
Requirements:
1. Output an array called segments.
2. For each segment set callType to either line or curve or point.
3. If the very first call begins with BEGINNING or COMMENCING at a <monument>:
   – Set locationDescription exactly to BEGINNING or COMMENCING.
   – **If that clause includes grid coordinates “N: X, E: Y” (numbers without degree symbols), parse X into baseNorth (number) and Y into baseEast (number).**
   – **If you see a geographic latitude/longitude pair (e.g., “N 031°40'19.87\" W 101°59'47.14\"” or “N 31.402315° W 101.5975465°”), DO NOT set baseNorth/baseEast; treat them as metadata and leave baseNorth=baseEast=null.**
   – Extract that <monument> into monument.
   – Always set bearing=null, distance=null and unit=null for this segment, even if THENCE follows.
   – Set callType as 'point'.
4. If callType == line (and locationDescription is not BEGINNING/COMMENCING):
   – Extract the compass bearing into bearing (e.g. North 74°57′16″ West).
   – Extract the straight distance into distance (number) and unit (string).
   – Optionally extract any narrative into description.
   – Optionally extract any monument into monument (excluding COMMENCING/BEGINNING monuments).
5. If callType == curve:
   – Extract the chord bearing into bearing.
   – Extract the chord length into distance and unit.
   – Extract the curve’s central angle into angle (DMS string or words).
   – Optionally extract any monument into monument.
6. If POINT OF BEGINNING, POINT OF EXIT or POINT OF REENTRY appears inside a THENCE:
   – Set locationDescription to that exact phrase.
   – Do NOT null out bearing/distance; extract them normally from the THENCE call.
7. After a POB, BEGINNING, COMMENCING or inside a THENCE if you see a 'from which …' clause:
   – Extract its bearing, distance, unit, and monument into pointOfReference.
   – If no 'from which …' clause, set pointOfReference=null.
8. Bearings, angles, and curve details may use symbols (°, ′, ″) or the words degrees, minutes, seconds — handle both.
9. Always include every top-level key in each segment; use null when a value is not present.
10. When you see a “courses and distances:” section, split each comma/semicolon/period/‘and N|S’ separated bearing-distance pair into its own line segment:
    – callType = line
    – Extract bearing, distance and unit normally
    – If a bearing appears to start with an 8 return an "S" as in SOUTH
    – Default monument = "point" if none supplied
    – locationDescription remains null unless overridden.
11. *Intermediate points on line*: If within a segment you encounter phrases like “at 250 feet passing a 1/2 inch iron rod” (or “at a distance of 250 feet passing …”), optionally followed by a 'from which' clause (e.g. “from which a nail bears 32°43'44\" a distance of 3 feet”), append an object to pointsOnLine:
    – pointsOnLine[i].distance = numeric intermediate distance (e.g. 250)
    – pointsOnLine[i].unit = its unit (e.g. feet)
    – pointsOnLine[i].monument = the monument passed (e.g. a 1/2 inch iron rod)
    – pointsOnLine[i].bearing = the parent segment’s bearing (reuse the segment bearing string)
    – If a 'from which' clause is attached to that intermediate monument, set pointsOnLine[i].pointOfReference with bearing/distance/unit/monument; else null.
    – Include *all* such intermediate points in order of appearance.
12. Do NOT place intermediate 'from which' monuments in the parent segment’s pointOfReference (they belong inside the matching pointsOnLine entry). Parent pointOfReference is only for the segment’s primary monument context.
13. **Only for the first segment when callType == "point": return two additional numeric fields — `baseNorth` and `baseEast` — pulled from explicit grid coord syntax like “N: <number>, E: <number>”, “Northing: <number>, Easting: <number>”, or similar numeric forms with no degree symbols.**
    **If the coordinates are expressed as lat/long (contain degree symbols or DMS words, or read like “N/S <deg>[.<decimals>]° … E/W <deg>[.<decimals>]°”), leave baseNorth=baseEast=null.**
14. **Bearings must always start with one of the letters N, S, E or W.**  **Do not substitute similar-looking digits** (e.g. '8' or '5') **in place of these letters.**
15. **Latitude/Longitude detection (IGNORE for baseNorth/baseEast):**
    Treat any pair like “N dd.ddddd° W ddd.ddddd°”, “N dd°mm'ss\" W ddd°mm'ss\"”, or the same inside parentheses, as geographic lat/long. Do not convert these into baseNorth/baseEast; do not treat them as bearings or pointsOnLine. Example:
    – “BEGINNING at a point having coordinates (N 031°40'19.87\" W 101°59'47.14\") …” ⇒ baseNorth=null, baseEast=null.
    – “BEGINNING at N: 6,987,123.45, E: 2,345,678.90 …” ⇒ baseNorth=6987123.45, baseEast=2345678.90.
"""

def build_messages_array(segments):
    """
    segments: list of dicts with keys 'prompt' and 'completion'
    returns: list where each item is a messages array:
             [ {role:system, ...}, {role:user, ...}, {role:assistant, ...} ]
    """
    messages = []
    for seg in segments:  # <- simple loop, no extra validation
        messages.append([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": seg.get("prompt", "")},
            {"role": "assistant", "content": seg.get("completion", "")},
        ])
    return messages

def main():
    ap = argparse.ArgumentParser(description="Build messages arrays from segments.")
    ap.add_argument("input", help="Input JSON file (either {\"segments\": [...]} or a raw list).")
    ap.add_argument("-o", "--output", help="Output JSON file (defaults to stdout).")
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    segs = data["segments"] if isinstance(data, dict) else data
    out = build_messages_array(segs)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
    else:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        print()

if __name__ == "__main__":
    main()
