SYSTEM_PROMPT_LINES = (
    "You are a land survey technician. Parse the deed text into a JSON object matching the EXTRACT_METES_BOUNDS_SCHEMA.\n" 
    "Requirements:\n" 
    "1. Output an array called segments.\n" 
    "2. For each segment set callType to either line or curve or point.\n" 
    "3. If the very first call begins with BEGINNING or COMMENCING at a <monument>:\n" 
    "   – Set locationDescription exactly to BEGINNING or COMMENCING.\n" 
    "   – **If that clause includes grid coordinates “N: X, E: Y” (numbers without degree symbols), parse X into baseNorth (number) and Y into baseEast (number).**\n" 
    "   – **If you see a geographic latitude/longitude pair (e.g., “N 031°40'19.87\" W 101°59'47.14\"” or “N 31.402315° W 101.5975465°”), DO NOT set baseNorth/baseEast; treat them as metadata and leave baseNorth=baseEast=null.**\n" 
    "   – Extract that <monument> into monument.\n" 
    "   – Always set bearing=null, distance=null and unit=null for this segment, even if THENCE follows.\n" 
    "   - Set callType as 'point'.\n" 
    "4. If callType == line (and locationDescription is not BEGINNING/COMMENCING):\n" 
    "   – Extract the compass bearing into bearing (e.g. North 74°57′16″ West).\n" 
    "   – Extract the straight distance into distance (number) and unit (string).\n" 
    "   – Optionally extract any narrative into description.\n" 
    "   – Optionally extract any monument into monument (excluding COMMENCING/BEGINNING monuments).\n" 
    "5. If callType == curve:\n" 
    "   – Extract the chord bearing into bearing.\n" 
    "   – Extract the chord length into distance and unit.\n" 
    "   – Extract the curve’s central angle into angle (DMS string or words).\n" 
    "   – Optionally extract any monument into monument.\n" 
    "6. If POINT OF BEGINNING, POINT OF EXIT or POINT OF REENTRY appears inside a THENCE:\n" 
    "   – Set locationDescription to that exact phrase.\n" 
    "   – Do NOT null out bearing/distance; extract them normally from the THENCE call.\n" 
    "7. After a POB, BEGINNING, COMMENCING or inside a THENCE if you see a 'from which …' clause:\n" 
    "   – Extract its bearing, distance, unit, and monument into pointOfReference.\n" 
    "   – If no 'from which …' clause, set pointOfReference=null.\n" 
    "8. Bearings, angles, and curve details may use symbols (°, ′, ″) or the words degrees, minutes, seconds — handle both.\n" 
    "9. Always include every top-level key in each segment; use null when a value is not present.\n" 
    "10. When you see a “courses and distances:” section, split each comma/semicolon/period/‘and N|S’ separated bearing-distance pair into its own line segment:\n" 
    "    – callType = line\n" 
    "    – Extract bearing, distance and unit normally\n" 
    "    – If a bearing appears to start with an 8 return an \"S\" as in SOUTH\n" 
    "    – Default monument = \"point\" if none supplied\n" 
    "    – locationDescription remains null unless overridden.\n" 
    "11. *Intermediate points on line*: If within a segment you encounter phrases like “at 250 feet passing a 1/2 inch iron rod” (or “at a distance of 250 feet passing …”), optionally followed by a 'from which' clause (e.g. “from which a nail bears 32°43'44\" a distance of 3 feet”), append an object to pointsOnLine:\n" 
    "    – pointsOnLine[i].distance = numeric intermediate distance (e.g. 250)\n" 
    "    – pointsOnLine[i].unit = its unit (e.g. feet)\n" 
    "    – pointsOnLine[i].monument = the monument passed (e.g. a 1/2 inch iron rod)\n" 
    "    – pointsOnLine[i].bearing = the parent segment’s bearing (reuse the segment bearing string)\n" 
    "    – If a 'from which' clause is attached to that intermediate monument, set pointsOnLine[i].pointOfReference with bearing/distance/unit/monument; else null.\n" 
    "    – Include *all* such intermediate points in order of appearance.\n" 
    "12. Do NOT place intermediate 'from which' monuments in the parent segment’s pointOfReference (they belong inside the matching pointsOnLine entry). Parent pointOfReference is only for the segment’s primary monument context.\n" 
    "13. **Only for the first segment when callType == \"point\": return two additional numeric fields — `baseNorth` and `baseEast` — pulled from explicit grid coord syntax like “N: <number>, E: <number>”, “Northing: <number>, Easting: <number>”, or similar numeric forms with no degree symbols.**\n" 
    "    **If the coordinates are expressed as lat/long (contain degree symbols or DMS words, or read like “N/S <deg>[.<decimals>]° … E/W <deg>[.<decimals>]°”), leave baseNorth=baseEast=null.**\n" 
    "14. **Bearings must always start with one of the letters N, S, E or W.**  **Do not substitute similar-looking digits** (e.g. '8' or '5') **in place of these letters.**\n" 
    "15. **Latitude/Longitude detection (IGNORE for baseNorth/baseEast):**\n" 
    "    Treat any pair like “N dd.ddddd° W ddd.ddddd°”, “N dd°mm'ss\" W ddd°mm'ss\"”, or the same inside parentheses, as geographic lat/long. Do not convert these into baseNorth/baseEast; do not treat them as bearings or pointsOnLine. Example:\n" 
    "    – “BEGINNING at a point having coordinates (N 031°40'19.87\" W 101°59'47.14\") …” ⇒ baseNorth=null, baseEast=null.\n" 
    "    – “BEGINNING at N: 6,987,123.45, E: 2,345,678.90 …” ⇒ baseNorth=6987123.45, baseEast=2345678.90.\n"
)


EXTRACT_METES_BOUNDS_SCHEMA = {
    "name": "extract_metes_bounds",
    "description": (
        "Extract each metes-and-bounds segment from a deed text into structured objects. "
        "Each segment must include callType ('line', 'curve', or 'point'), bearing, distance, unit, "
        "monument, pointOfReference (which may be null), and optionally pointsOnLine (array of "
        "intermediate passed monuments)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "description": "Ordered list of metes-and-bounds calls.",
                "items": {
                    "type": "object",
                    "properties": {
                        "locationDescription": {
                            "type": ["string", "null"],
                            "enum": [
                                "COMMENCING",
                                "BEGINNING",
                                "POINT OF BEGINNING",
                                "POINT OF TERMINATION",
                                "POINT OF EXIT",
                                "POINT OF REENTRY"
                            ],
                            "description": (
                                "If first call begins with BEGINNING or COMMENCING, set to that phrase and "
                                "null-out bearing/distance/unit. If POB/EXIT/REENTRY appears inside a THENCE "
                                "call, set to that phrase but still extract bearing/distance normally. "
                                "If 'BEGINNING' refers to 'BEGINNING OF A CURVE TO THE RIGHT', leave null."
                            )
                        },
                        "callType": {
                            "type": "string",
                            "enum": ["line", "curve", "point"],
                            "description": "Whether this segment is a straight line, a curve, or a point-only clause."
                        },
                        "bearing": {
                            "type": ["string", "null"],
                            "description": (
                                "For line: the compass bearing. For curve: the chord bearing. "
                                "Null for COMMENCING/BEGINNING point segments."
                            )
                        },
                        "description": {
                            "type": ["string", "null"],
                            "description": "Optional narrative clause between bearing and distance."
                        },
                        "distance": {
                            "type": ["number", "null"],
                            "description": (
                                "For line: straight-line distance. For curve: chord length. "
                                "Null for COMMENCING/BEGINNING point segments."
                            )
                        },
                        "unit": {
                            "type": ["string", "null"],
                            "description": "Distance unit, e.g., 'feet', 'chains'."
                        },
                        "angle": {
                            "type": ["string", "null"],
                            "description": "Only for curve: the central angle (DMS or words)."
                        },
                        "direction": {
                            "type": ["string", "null"],
                            "description": "Only for curve: 'right' or 'left'."
                        },
                        "arcDistance": {
                            "type": ["string", "null"],
                            "description": "Only for curve: arc length."
                        },
                        "radius": {
                            "type": ["string", "null"],
                            "description": "Only for curve: radius."
                        },
                        "baseNorth": {
                            "type": ["number", "null"],
                            "description": "Only for the first point segment: N: northing grid coordinate."
                        },
                        "baseEast": {
                            "type": ["number", "null"],
                            "description": "Only for the first point segment: E: easting grid coordinate."
                        },
                        "monument": {
                            "type": ["string", "null"],
                            "description": (
                                "Monument at the segment terminus or following a COMMENCING/BEGINNING clause. "
                                "Default to 'point' in courses-and-distances if none is supplied."
                            )
                        },
                        "pointOfReference": {
                            "type": ["object", "null"],
                            "description": (
                                "If a 'from which' clause appears for the primary monument, extract its "
                                "bearing, distance, unit, and monument here; otherwise null."
                            ),
                            "properties": {
                                "bearing":  {"type": "string"},
                                "distance": {"type": "number"},
                                "unit":     {"type": "string"},
                                "monument": {"type": "string"}
                            },
                            "required": ["bearing", "distance", "unit", "monument"]
                        },
                        "pointsOnLine": {
                            "type": "array",
                            "description": (
                                "Zero or more intermediate monuments passed within the segment "
                                "(e.g., 'at 250 feet passing a 1/2 inch iron rod')."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "bearing":  {"type": ["string", "null"], "description": "Reuse parent bearing."},
                                    "distance": {"type": "number", "description": "Intermediate distance."},
                                    "unit":     {"type": "string", "description": "Unit for the intermediate distance."},
                                    "monument": {"type": "string", "description": "Monument being passed."},
                                    "pointOfReference": {
                                        "type": ["object", "null"],
                                        "description": "Optional 'from which' clause for the intermediate monument.",
                                        "properties": {
                                            "bearing":  {"type": "string"},
                                            "distance": {"type": "number"},
                                            "unit":     {"type": "string"},
                                            "monument": {"type": "string"}
                                        },
                                        "required": ["bearing", "distance", "unit", "monument"]
                                    }
                                },
                                "required": ["bearing", "distance", "unit", "monument", "pointOfReference"]
                            }
                        }
                    },
                    "required": [
                        "callType",
                        "bearing",
                        "distance",
                        "unit",
                        "monument",
                        "pointOfReference",
                        "pointsOnLine",
                        "baseNorth",
                        "baseEast",
                        "locationDescription",
                        "angle",
                        "direction",
                        "arcDistance",
                        "radius",
                        "description"
                    ]
                }
            }
        },
        "required": ["segments"]
    }
}


INVENTORY_SCHEMA_FUNCTION = {
    "name": "extract_inventory",
    "description": "Extract the initial inventory clause from a deed description.",
    "parameters": {
        "type": "object",
        "properties": {
            "acreage": {
                "type": "number",
                "description": "Acreage of the parcel"
            },
            "acreageUnit": {
                "type": "string",
                "description": "Unit of the acreage, e.g. 'acre'"
            },
            "originalSurvey": {
                "type": ["string", "null"],
                "description": "Name of the original survey or league"
            },
            "abstract": {
                "type": ["string", "null"],
                "description": "Abstract number"
            },
            "county": {
                "type": "string",
                "description": "County name"
            },
            "state": {
                "type": "string",
                "description": "State name"
            },
            "date": {
                "type": ["string", "null"],
                "description": "Date of the referenced deed"
            },
            "grantee": {
                "type": ["string", "null"],
                "description": "Name of the grantee"
            },
            "grantor": {
                "type": ["string", "null"],
                "description": "Name of the grantor"
            },
            "volume": {
                "type": ["integer", "null"],
                "description": "Volume number in the public records"
            },
            "page": {
                "type": ["integer", "null"],
                "description": "Page number in the public records"
            },
            "sourceOfRecords": {
                "type": ["string", "null"],
                "description": "Name of the records source, e.g. 'Wharton County Official Records'"
            }
        },
        "required": [
            "acreage", "acreageUnit", "county", "state", "sourceOfRecords"
        ]
    }
}

SYSTEM_PROMPT_INVENTORY = (
    "You are a land survey technician.  Before the metes-and-bounds calls begins "
    "there is an inventory clause describing the parcel.  Extract exactly the following fields:\n"
    "  • acreage (number)\n"
    "  • acreageUnit (string, e.g. 'acre')\n"
    "  • originalSurvey (string or null)\n"
    "  • abstract (string or null)\n"
    "  • county (string)\n"
    "  • state (string)\n"
    "  • date (string or null, e.g. 'March 09, 2001')\n"
    "  • grantee (string or null)\n"
    "  • grantor (string or null)\n"
    "  • volume (integer or null)\n"
    "  • page (integer or null)\n"
    "  • sourceOfRecords (string or null)\n"
    "If any piece is missing, return null for that key.  Return a single JSON object."
)
