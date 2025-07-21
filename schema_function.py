SYSTEM_PROMPT_LINES = (
    "You are a land survey technician. Parse the deed text into a JSON object matching the EXTRACT_METES_BOUNDS_SCHEMA.\n"
    "Requirements:\n"
    "1. Output an array called segments.\n"
    "2. For each segment set callType to either line or curve.\n"
    "3. If the very first call begins with BEGINNING or COMMENCING at a <monument>:\n"
    "   – Set locationDescription exactly to BEGINNING or COMMENCING.\n"
    "   – Extract that <monument> into monument.\n"
    "   – Always set bearing=null, distance=null and unit=null for this segment, even if THENCE follows.\n"
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
    "7. After a POB, BEGINNING, COMMENCING or inside a THENCE if you see a from which… clause:\n"
    "   – Extract its bearing, distance, unit, and monument into pointOfReference.\n"
    "   – If no from which… clause, set pointOfReference=null.\n"
    "8. Bearings, angles, and curve details may use symbols (°, ′, ″) or the words degrees, minutes, seconds — handle both.\n"
    "9. Always include every top-level key in each segment; use null when a value is not present.\n"
    "10. When you see a “courses and distances:” section, take everything from that phrase up to the next non‑bearing sentence, then **split** it into segments by ANY of:  \n"
    "    – commas\n"
    "    – semicolons\n"
    "    – periods\n"
    "    – the word “and” when it precedes a compass direction (N or S)\n"
    "    – **commas** (`,`)\n"
    "    – **semicolons** (`;`)\n"
    "    – **periods** (`.`)\n"
    "    – the literal word **“and”** when it immediately precedes a compass direction (e.g. “and N ...”)  \n"      "    – For each piece, set `callType = line`, extract its `bearing`, `distance`, and `unit` normally, default `monument = \"point\"`, leave `locationDescription = null` unless over‑ruled by a POB/BEGINNING.\n"
)




EXTRACT_METES_BOUNDS_SCHEMA = {
    "name": "extract_metes_bounds",
    "description": (
        "Extract each metes-and-bounds segment from a deed text into structured "
        "objects. Each segment must include callType ('line' or 'curve'), "
        "bearing, distance, unit, and pointOfReference (which may be null). "
        "If callType is 'curve', also include angle."
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
                                "If the first call begins with 'BEGINNING' or 'COMMENCING', "
                                "set to that exact phrase and null out the top-level bearing and distance. "
                                "If 'POINT OF BEGINNING', 'POINT OF EXIT' or 'POINT OF REENTRY' appears "
                                "inside a THENCE call, set to that exact phrase but still extract the "
                                "bearing and distance from that THENCE call."
                            )
                        },
                        "callType": {
                            "type": "string",
                            "enum": ["line", "curve"],
                            "description": "Whether this segment is a straight line or a curve."
                        },
                        "bearing": {
                            "type": ["string", "null"],
                            "description": (
                                "For line: the compass bearing. "
                                "For curve: the chord bearing. "
                                "If locationDescription is 'BEGINNING' or 'COMMENCING', null."
                            )
                        },
                        "description": {
                            "type": ["string", "null"],
                            "description": "Optional narrative clause between bearing and distance."
                        },
                        "distance": {
                            "type": ["number", "null"],
                            "description": (
                                "For line: the straight-line distance. "
                                "For curve: the chord length. "
                                "If locationDescription is 'BEGINNING' or 'COMMENCING', null."
                            )
                        },
                        "unit": {
                            "type": ["string", "null"],
                            "description": "Distance unit, e.g. 'feet', 'chains'."
                        },
                        "angle": {
                            "type": ["string", "null"],
                            "description": (
                                "Only for curve: the central angle of the curve in DMS "
                                "(symbols or the words 'degrees', 'minutes', 'seconds')."
                            )
                        },
                        "monument": {
                            "type": ["string", "null"],
                            "description": (
                                "Monument at the end of this call or following a COMMENCING/BEGINNING clause. "
                                "Physical monument at terminus. In a courses-and-distances list, default missing monuments to 'point'. Do not include 'from which' monuments here."
                            )
                        },
                        "pointOfReference": {
                            "type": ["object", "null"],
                            "description": (
                                "If a 'from which' clause appears (after a POB, COMMENCING, "
                                "or within a THENCE), extract its bearing, distance, unit, and monument here. "
                                "If no such clause, set to null."
                            ),
                            "properties": {
                                "bearing": {
                                    "type": "string",
                                    "description": "Bearing from the reference clause."
                                },
                                "distance": {
                                    "type": "number",
                                    "description": "Distance from the reference clause."
                                },
                                "unit": {
                                    "type": "string",
                                    "description": "Unit for the reference distance."
                                },
                                "monument": {
                                    "type": "string",
                                    "description": "Monument description in the reference clause."
                                }
                            },
                            "required": ["bearing", "distance", "unit", "monument"]
                        }
                    },
                    "required": ["callType", "distance", "unit", "pointOfReference"]
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
