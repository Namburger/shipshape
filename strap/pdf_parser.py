"""Zero-dependency parser for Ship Shape Marathon Training Schedule PDFs."""

import datetime
import re
import zlib
from typing import Dict, List, Optional, Tuple


_MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_BOILERPLATE_LINES = (
    "Plus 1 mi warmup, 1 mi cool down",
    "and count as Easy miles",
    "All weeks start",
    "with Monday",
    "(3WO=Tue,Thur,Sat. workouts)",
)


def _unescape_pdf_string(raw: str) -> str:
    """Decode PDF literal string escape sequences."""
    out: List[str] = []
    i = 0
    length = len(raw)
    escapes = {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "b": "\b",
        "f": "\f",
        "(": "(",
        ")": ")",
        "\\": "\\",
    }
    while i < length:
        ch = raw[i]
        if ch == "\\":
            i += 1
            if i >= length:
                break
            esc = raw[i]
            if esc in escapes:
                out.append(escapes[esc])
            elif esc in "\r\n":
                if esc == "\r" and i + 1 < length and raw[i + 1] == "\n":
                    i += 1
            elif esc in "01234567":
                oct_digits = esc
                for _ in range(2):
                    if i + 1 < length and raw[i + 1] in "01234567":
                        i += 1
                        oct_digits += raw[i]
                    else:
                        break
                out.append(chr(int(oct_digits, 8)))
            else:
                out.append(esc)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _extract_pdf_streams(pdf_bytes: bytes) -> List[str]:
    """Decompress FlateDecode content streams (or keep raw text streams)."""
    streams: List[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.S):
        payload = match.group(1)
        decoded: Optional[str] = None
        try:
            decoded = zlib.decompress(payload).decode("latin1")
        except zlib.error:
            if b"BT" in payload and b"ET" in payload:
                decoded = payload.decode("latin1", errors="replace")
        if decoded and ("BT" in decoded or " re" in decoded):
            streams.append(decoded)
    return streams


def _extract_spans_and_rects(
    streams: List[str],
) -> Tuple[List[Tuple[float, float, str]], List[Tuple[float, float, float, float]]]:
    """Extract positioned text spans `(x, y, text)` and border rectangles `(x, y, w, h)`."""
    spans: List[Tuple[float, float, str]] = []
    rects: List[Tuple[float, float, float, float]] = []

    tm_re = re.compile(
        r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+"
        r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+Tm$"
    )
    td_re = re.compile(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+T[dD]$")
    tl_re = re.compile(r"(-?\d+(?:\.\d+)?)\s+TL$")
    rect_re = re.compile(
        r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+re\b"
    )
    lit_re = re.compile(r"\(((?:\\.|[^\\)])*)\)")

    for stream in streams:
        for rm in rect_re.finditer(stream):
            rects.append(
                (float(rm.group(1)), float(rm.group(2)), float(rm.group(3)), float(rm.group(4)))
            )

        for bt in re.finditer(r"BT(.*?)ET", stream, re.S):
            x, y = 0.0, 0.0
            leading = 0.0
            for raw_line in bt.group(1).splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                m_tm = tm_re.search(line)
                if m_tm:
                    x = float(m_tm.group(5))
                    y = float(m_tm.group(6))
                    continue
                m_td = td_re.search(line)
                if m_td:
                    x += float(m_td.group(1))
                    y += float(m_td.group(2))
                    if line.endswith("TD"):
                        leading = -float(m_td.group(2))
                    continue
                m_tl = tl_re.search(line)
                if m_tl:
                    leading = float(m_tl.group(1))
                    continue
                if line == "T*":
                    y -= leading
                    continue
                if line.endswith("TJ") or line.endswith("Tj") or line.endswith("'"):
                    if line.endswith("'"):
                        y -= leading
                    parts = lit_re.findall(line)
                    if parts:
                        text = "".join(_unescape_pdf_string(p) for p in parts)
                        if text.strip():
                            spans.append((x, y, text))

    return spans, rects


def _group_cell_lines(cell_spans: List[Tuple[float, float, str]]) -> List[str]:
    """Sort spans within a cell top-to-bottom, left-to-right and join wrapped fragments."""
    ordered = sorted(cell_spans, key=lambda sp: (-round(sp[1], 1), sp[0]))
    lines: List[str] = []
    current_y: Optional[float] = None
    for _, y, text in ordered:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            continue
        if current_y is None or abs(y - current_y) > 2.5:
            lines.append(cleaned)
            current_y = y
        else:
            lines[-1] = f"{lines[-1]} {cleaned}"
    return lines


def _normalize_pace(pace_str: str) -> str:
    """Normalize `0:11:12` or `8:54` into zero-padded `MM:SS`."""
    parts = pace_str.strip().split(":")
    if len(parts) == 3 and parts[0] == "0":
        mm, ss = int(parts[1]), int(parts[2])
        return f"{mm:02d}:{ss:02d}"
    if len(parts) == 2:
        mm, ss = int(parts[0]), int(parts[1])
        return f"{mm:02d}:{ss:02d}"
    return pace_str.strip()


def _normalize_full_time(time_str: str) -> str:
    """Return consistent `H:MM:SS` or `0:MM:SS` string."""
    parts = [p.strip() for p in time_str.strip().split(":")]
    if len(parts) == 3:
        return f"{int(parts[0])}:{int(parts[1]):02d}:{int(parts[2]):02d}"
    if len(parts) == 2:
        return f"0:{int(parts[0]):02d}:{int(parts[1]):02d}"
    return time_str.strip()


def _parse_created_date(text: str) -> str:
    """Convert `MM/DD/YYYY` into ISO `YYYY-MM-DD`, defaulting to today on fallback."""
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return datetime.date(year, month, day).isoformat()
    return datetime.date.today().isoformat()


def _resolve_day_month(
    day: int, month_abbr: str, anchor: datetime.date
) -> datetime.date:
    """Resolve `DD-Mon` or `DD Mon` to the closest calendar date on or after anchor."""
    month = _MONTH_MAP[month_abbr[:3].lower()]
    for year in (anchor.year, anchor.year + 1):
        try:
            candidate = datetime.date(year, month, day)
        except ValueError:
            continue
        if (candidate - anchor).days >= -14:
            return candidate
    return datetime.date(anchor.year, month, day)


def _strip_boilerplate(lines: List[str]) -> List[str]:
    """Remove recurring instructional header/note phrases from cell lines."""
    kept: List[str] = []
    for line in lines:
        cleaned = line
        for phrase in _BOILERPLATE_LINES:
            if cleaned.strip() == phrase:
                cleaned = ""
                break
        if cleaned.strip():
            kept.append(cleaned.strip())
    return kept


def _clean_number(val: float) -> float:
    """Round floats cleanly to avoid floating-point artifacts like 3.50000001."""
    rounded = round(val, 2)
    return float(int(rounded)) if rounded == int(rounded) else rounded


def _parse_easy_day(lines: List[str]) -> dict:
    """Parse Monday/Wednesday easy run cell."""
    joined = " ".join(lines)
    dist_m = re.search(r"([\d.]+)\s*miles?", joined, re.I)
    pace_m = re.search(r"@\s*(\d+:\d{2}(?::\d{2})?)", joined)
    distance = _clean_number(float(dist_m.group(1))) if dist_m else 0.0
    raw_pace = pace_m.group(1) if pace_m else ""
    return {
        "type": "Easy",
        "distance_mi": distance,
        "target_pace": _normalize_pace(raw_pace) if raw_pace else None,
        "description": re.sub(r"\s+", " ", joined).strip(),
    }


def _parse_tuesday_speed(raw_lines: List[str]) -> dict:
    """Parse Tuesday speed work cell (intervals, pyramids, or fartlek)."""
    lines = _strip_boilerplate(raw_lines)
    joined = re.sub(r"\s+", " ", " ".join(lines)).strip()

    # 1. Repeated intervals: `7 x 800m @ 0:04:29 (Yasso 800's)`
    rep_m = re.search(
        r"(\d+)\s*[xX]\s*(\d+)\s*m\s*@\s*(\d+:\d{2}(?::\d{2})?)", joined
    )
    if rep_m:
        reps = int(rep_m.group(1))
        meters = int(rep_m.group(2))
        pace_norm = _normalize_pace(rep_m.group(3))
        workout_mi = _clean_number((reps * meters) / 1600.0)
        target_pace = pace_norm if meters == 1600 else f"{pace_norm}/{meters}m"
        return {
            "type": "Speed Work",
            "workout_mi": workout_mi,
            "warmup_cooldown_mi": 2.0,
            "total_mi": _clean_number(workout_mi + 2.0),
            "target_pace": target_pace,
            "description": joined,
        }

    # 2. Pyramid track ladder: `400m @ 0:02:00`, `800m @ 0:04:07`, `1600m @ 0:08:43`...
    step_matches = re.findall(r"(\d+)\s*m\s*@\s*(\d+:\d{2}(?::\d{2})?)", joined)
    if step_matches:
        total_meters = sum(int(m) for m, _ in step_matches)
        workout_mi = _clean_number(total_meters / 1600.0)
        mile_paces = [_normalize_pace(p) for m, p in step_matches if int(m) == 1600]
        target_pace = mile_paces[0] if mile_paces else None
        desc = ", ".join(line.strip() for line in lines if line.strip())
        return {
            "type": "Speed Work",
            "workout_mi": workout_mi,
            "warmup_cooldown_mi": 2.0,
            "total_mi": _clean_number(workout_mi + 2.0),
            "target_pace": target_pace,
            "description": desc,
        }

    # 3. Distance-based fartlek / speed workout (e.g. `Kenyan Style Fartlek 3 miles`)
    dist_m = re.search(r"([\d.]+)\s*miles?", joined, re.I)
    pace_m = re.search(r"@\s*(\d+:\d{2}(?::\d{2})?)", joined)
    workout_mi = _clean_number(float(dist_m.group(1))) if dist_m else 0.0
    target_pace = _normalize_pace(pace_m.group(1)) if pace_m else None
    return {
        "type": "Speed Work",
        "workout_mi": workout_mi,
        "warmup_cooldown_mi": 2.0,
        "total_mi": _clean_number(workout_mi + 2.0),
        "target_pace": target_pace,
        "description": joined,
    }


def _parse_thursday_tempo_hill(raw_lines: List[str]) -> dict:
    """Parse Thursday Tempo Run or Hill Repeats cell."""
    lines = _strip_boilerplate(raw_lines)
    joined = re.sub(r"\s+", " ", " ".join(lines)).strip()
    is_hill = "hill" in joined.lower() and "tempo" not in joined.lower()
    workout_type = "Hill" if is_hill else "Tempo"

    dist_m = re.search(r"([\d.]+)\s*miles?", joined, re.I)
    pace_m = re.search(r"@\s*(\d+:\d{2}(?::\d{2})?)", joined)
    workout_mi = _clean_number(float(dist_m.group(1))) if dist_m else 0.0
    target_pace = _normalize_pace(pace_m.group(1)) if pace_m else None

    return {
        "type": workout_type,
        "workout_mi": workout_mi,
        "warmup_cooldown_mi": 2.0,
        "total_mi": _clean_number(workout_mi + 2.0),
        "target_pace": target_pace,
        "description": joined,
    }


def _parse_saturday_long_run(
    lines: List[str], week_of: datetime.date
) -> Tuple[dict, Optional[str], Optional[str]]:
    """Parse Saturday long run or goal marathon race cell.

    Returns `(day_dict, race_name_if_race, race_date_iso_if_race)`.
    """
    joined = re.sub(r"\s+", " ", " ".join(lines)).strip()
    segs = re.findall(
        r"([\d.]+)\s*miles?\s*@\s*(\d+:\d{2}(?::\d{2})?)", joined, re.I
    )

    is_race = "26.2" in joined or (
        "marathon" in joined.lower() and "half marathon" not in joined.lower()
    )
    race_name: Optional[str] = None
    race_date: Optional[str] = None

    if is_race and lines:
        header_line = lines[0]
        if not re.match(r"^[\d.]+\s*miles?", header_line, re.I):
            date_in_line = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,})\b", header_line)
            if date_in_line and date_in_line.group(2)[:3].lower() in _MONTH_MAP:
                r_day = int(date_in_line.group(1))
                r_mon = date_in_line.group(2)
                race_date = _resolve_day_month(r_day, r_mon, week_of).isoformat()
                race_name = header_line[: date_in_line.start()].strip(" -:")
            else:
                race_name = header_line.strip()
        if not race_date:
            race_date = (week_of + datetime.timedelta(days=6)).isoformat()

    if segs:
        total_dist = _clean_number(sum(float(d) for d, _ in segs))
        paces = [_normalize_pace(p) for _, p in segs]
        target_pace = " / ".join(paces)
    else:
        dist_m = re.search(r"([\d.]+)\s*miles?", joined, re.I)
        total_dist = _clean_number(float(dist_m.group(1))) if dist_m else 0.0
        target_pace = None

    day_type = "Race" if is_race else "Long Run"
    desc = joined
    if is_race and len(lines) >= 2 and race_name:
        desc = f"{lines[0]}: {' '.join(lines[1:])}"

    return (
        {
            "type": day_type,
            "distance_mi": total_dist,
            "target_pace": target_pace,
            "description": desc,
        },
        race_name,
        race_date,
    )


def _parse_summary(lines: List[str], days: Dict[str, dict]) -> dict:
    """Parse Weekly Miles cell (`3 WO = ...`, `Easy = ...`, `Total = ...`)."""
    joined = " ".join(lines)
    wo_m = re.search(r"3\s*WO\s*=\s*([\d.]+)", joined, re.I)
    easy_m = re.search(r"Easy\s*=\s*([\d.]+)", joined, re.I)
    total_m = re.search(r"Total\s*=\s*([\d.]+)", joined, re.I)

    computed_wo = (
        days["Tuesday"].get("workout_mi", 0.0)
        + days["Thursday"].get("workout_mi", 0.0)
        + days["Saturday"].get("distance_mi", 0.0)
    )
    computed_easy = (
        days["Monday"].get("distance_mi", 0.0)
        + days["Wednesday"].get("distance_mi", 0.0)
        + days["Tuesday"].get("warmup_cooldown_mi", 0.0)
        + days["Thursday"].get("warmup_cooldown_mi", 0.0)
    )

    wo = _clean_number(float(wo_m.group(1))) if wo_m else _clean_number(computed_wo)
    easy = (
        _clean_number(float(easy_m.group(1)))
        if easy_m
        else _clean_number(computed_easy)
    )
    total = (
        _clean_number(float(total_m.group(1)))
        if total_m
        else _clean_number(wo + easy)
    )
    return {
        "workout_miles": wo,
        "easy_miles": easy,
        "total_miles": total,
    }


def _parse_metadata(
    spans: List[Tuple[float, float, str]], header_top_y: float
) -> dict:
    """Extract schedule metadata and target equivalents from the header region."""
    header_spans = sorted(
        [sp for sp in spans if sp[1] > header_top_y], key=lambda s: (-round(s[1], 1), s[0])
    )
    header_text = " ".join(sp[2].strip() for sp in header_spans)

    sched_m = re.search(r"\b(Sch[A-Za-z0-9]+)\b", header_text)
    schedule_id = sched_m.group(1) if sched_m else None

    created_date = _parse_created_date(header_text)

    # Find athlete right after `Developed for:` on the same row
    athlete = None
    dev_span = next((sp for sp in header_spans if "Developed for" in sp[2]), None)
    if dev_span:
        same_row = [
            sp
            for sp in header_spans
            if abs(sp[1] - dev_span[1]) < 4.0
            and sp[0] > dev_span[0]
            and not re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", sp[2].strip())
        ]
        if same_row:
            same_row.sort(key=lambda s: s[0])
            athlete = same_row[0][2].strip()

    program = (
        "Ship Shape Marathon Training Schedule"
        if "Marathon Training Schedule" in header_text
        else "Training Schedule"
    )

    # Extract Total Time row and Pace per mile row left-to-right
    def _row_values(label_prefix: str) -> List[str]:
        anchor = next(
            (sp for sp in header_spans if sp[2].strip().startswith(label_prefix)), None
        )
        if not anchor:
            return []
        vals = [
            sp
            for sp in header_spans
            if abs(sp[1] - anchor[1]) < 3.0
            and sp[0] > anchor[0]
            and re.match(r"^\d+:\d{2}:\d{2}$", sp[2].strip())
        ]
        vals.sort(key=lambda s: s[0])
        return [_normalize_full_time(sp[2]) for sp in vals]

    times = _row_values("Total Time")
    paces = _row_values("Pace per mile")

    equivalents: Dict[str, dict] = {}
    keys = ["5K", "10K", "half_marathon", "marathon", "boston_qualifying"]
    for idx, key in enumerate(keys):
        t_val = times[idx] if idx < len(times) else None
        p_val = paces[idx] if idx < len(paces) else None
        if t_val or p_val:
            equivalents[key] = {"time": t_val, "pace_per_mile": p_val}

    return {
        "program": program,
        "schedule_id": schedule_id,
        "athlete": athlete,
        "created_date": created_date,
        "start_date": None,
        "race_name": None,
        "race_date": None,
        "equivalents": equivalents,
        "notes": [
            "All weeks start with Monday.",
            "Tuesday Speed Work and Thursday Tempo/Hill Runs include +1 mi warmup and +1 mi cool down (counted toward weekly Easy miles).",
            "Weekly 3 WO miles = Tuesday workout distance + Thursday workout distance + Saturday long run distance.",
        ],
    }


def parse_training_plan_pdf(pdf_bytes: bytes) -> dict:
    """Parse a Ship Shape Marathon Training Schedule PDF into dashboard JSON schema."""
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("Uploaded file is not a valid PDF document.")

    streams = _extract_pdf_streams(pdf_bytes)
    if not streams:
        raise ValueError("Could not extract readable text streams from the PDF.")

    spans, rects = _extract_spans_and_rects(streams)
    if not spans:
        raise ValueError("No text found in the uploaded PDF.")

    # Locate the schedule grid using table divider rectangles
    raw_ys = [
        y
        for x, y, w, h in rects
        if w > 400 and 0 < h < 5 and 70 < y < 635
    ]
    raw_xs = [
        x
        for x, y, w, h in rects
        if h > 400 and 0 < w < 5
    ]

    ys = sorted(set(round(y, 2) for y in raw_ys), reverse=True)
    xs = sorted(set(round(x, 2) for x in raw_xs))

    # Deduplicate near-identical border coordinates within 1.5pt
    def _dedupe(vals: List[float]) -> List[float]:
        out: List[float] = []
        for v in vals:
            if not out or abs(v - out[-1]) > 1.5:
                out.append(v)
        return out

    ys = _dedupe(ys)
    xs = _dedupe(xs)

    if len(ys) < 2 or len(xs) != 9:
        raise ValueError(
            "Unsupported PDF layout: expected an 8-column Ship Shape training schedule table."
        )

    metadata = _parse_metadata(spans, header_top_y=ys[0] + 10.0)
    created_anchor = datetime.date.fromisoformat(metadata["created_date"])
    prev_monday: Optional[datetime.date] = None

    weeks: List[dict] = []
    race_name_found: Optional[str] = None
    race_date_found: Optional[str] = None

    for row_idx in range(len(ys) - 1):
        y_top, y_bot = ys[row_idx], ys[row_idx + 1]
        cells: List[List[str]] = []
        for col_idx in range(8):
            x_left, x_right = xs[col_idx], xs[col_idx + 1]
            cell_spans = [
                sp
                for sp in spans
                if x_left <= sp[0] + 1.0 < x_right and y_bot <= sp[1] < y_top
            ]
            cells.append(_group_cell_lines(cell_spans))

        week_num_text = "".join(cells[0]).strip()
        if not week_num_text.isdigit():
            continue
        week_number = int(week_num_text)

        week_of_lines = _strip_boilerplate(cells[1])
        label = week_of_lines[-1] if week_of_lines else ""
        date_m = re.search(r"(\d{1,2})-([A-Za-z]{3})", label)
        if date_m:
            anchor = prev_monday if prev_monday else created_anchor
            week_monday = _resolve_day_month(
                int(date_m.group(1)), date_m.group(2), anchor
            )
        elif prev_monday:
            week_monday = prev_monday + datetime.timedelta(days=7)
        else:
            raise ValueError(f"Could not parse WEEK OF date in week {week_number}.")
        prev_monday = week_monday

        sat_day, r_name, r_date = _parse_saturday_long_run(cells[6], week_monday)
        if r_name and not race_name_found:
            race_name_found = r_name
        if r_date and not race_date_found:
            race_date_found = r_date

        days = {
            "Monday": _parse_easy_day(cells[2]),
            "Tuesday": _parse_tuesday_speed(cells[3]),
            "Wednesday": _parse_easy_day(cells[4]),
            "Thursday": _parse_thursday_tempo_hill(cells[5]),
            "Saturday": sat_day,
        }
        summary = _parse_summary(cells[7], days)

        weeks.append(
            {
                "week_number": week_number,
                "week_of": week_monday.isoformat(),
                "label": label,
                "days": days,
                "summary": summary,
            }
        )

    if not weeks:
        raise ValueError("No training weeks could be parsed from the uploaded PDF.")

    metadata["start_date"] = weeks[0]["week_of"]
    if race_name_found:
        metadata["race_name"] = race_name_found
    else:
        metadata["race_name"] = "Marathon Goal"
    if race_date_found:
        metadata["race_date"] = race_date_found
    else:
        last_monday = datetime.date.fromisoformat(weeks[-1]["week_of"])
        metadata["race_date"] = (last_monday + datetime.timedelta(days=6)).isoformat()

    return {
        "metadata": metadata,
        "weeks": weeks,
    }
