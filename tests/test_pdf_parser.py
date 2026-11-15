"""Unit tests for strap.pdf_parser."""

import unittest
import zlib
from pathlib import Path

from strap.pdf_parser import parse_training_plan_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent
JANE_PDF = REPO_ROOT / "assets" / "jane_doe_training.pdf"
JOHN_PDF = REPO_ROOT / "assets" / "john_doe_training.pdf"


def _build_synthetic_shipshape_pdf() -> bytes:
    """Create a minimal valid single-stream PDF mimicking Ship Shape layout."""
    xs = [32.0, 62.0, 110.0, 142.0, 250.0, 284.0, 387.0, 472.0, 551.0]
    ys = [629.0, 589.0, 559.0]  # Header top divider -> Week 2 -> Week 1

    commands = []
    # Vertical grid lines
    for x in xs:
        commands.append(f"{x} 80.0 0.6 549.0 re f")
    # Horizontal grid lines
    for y in ys:
        commands.append(f"33.0 {y} 519.0 0.6 re f")

    spans = [
        # Metadata header
        (215.0, 702.0, "Marathon Training Schedule"),
        (526.0, 719.0, "SchX999"),
        (225.0, 686.6, "Developed for:"),
        (285.0, 686.6, "Jane Doe"),
        (476.0, 686.6, "08/01/2026"),
        (143.9, 653.7, "Total Time:"),
        (254.9, 653.7, "0:24:00"),
        (295.8, 653.7, "0:50:00"),
        (347.6, 653.7, "1:52:00"),
        (400.5, 653.7, "3:55:00"),
        (143.9, 643.9, "Pace per mile of:"),
        (254.9, 643.9, "0:07:43"),
        (295.8, 643.9, "0:08:03"),
        (347.6, 643.9, "0:08:33"),
        (400.5, 643.9, "0:08:58"),
        # Row 1: Week 2 (Dec 28 -> crosses year boundary into Week 1 Jan 4)
        (40.0, 610.0, "2"),
        (70.0, 610.0, "28-Dec"),
        (115.0, 610.0, "5 miles"),
        (115.0, 600.0, "Easy @ 0:09:50"),
        (150.0, 610.0, "4 x 1600m @ 0:07:43"),
        (255.0, 610.0, "4 miles"),
        (255.0, 600.0, "Easy @ 0:09:50"),
        (290.0, 610.0, "Tempo Run"),
        (290.0, 600.0, "5 miles @ 0:08:15"),
        (395.0, 610.0, "10 miles @ 0:09:15"),
        (395.0, 600.0, "and then"),
        (395.0, 592.0, "4 miles @ 0:08:58"),
        (480.0, 610.0, "3 WO = 23"),
        (480.0, 600.0, "Easy = 13"),
        (480.0, 592.0, "Total = 36"),
        # Row 2: Week 1 (4-Jan, next year 2027)
        (40.0, 575.0, "1"),
        (70.0, 575.0, "4-Jan"),
        (115.0, 575.0, "3 miles Easy @ 0:09:50"),
        (150.0, 575.0, "8 x 400m @ 0:01:45"),
        (255.0, 575.0, "3 miles Easy @ 0:09:50"),
        (290.0, 575.0, "Hill Repeats 3 miles"),
        (395.0, 575.0, "Houston Marathon 10 Jan"),
        (395.0, 565.0, "26.2 miles @ 0:08:58"),
        (480.0, 575.0, "3 WO = 31.2"),
        (480.0, 565.0, "Easy = 10"),
        (480.0, 559.5, "Total = 41.2"),
    ]
    for x, y, text in spans:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"BT\n1 0 0 1 {x} {y} Tm\n({escaped}) Tj\nET")

    compressed = zlib.compress("\n".join(commands).encode("latin1"))
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Length " + str(len(compressed)).encode() + b" /Filter /FlateDecode >>\n"
        b"stream\n" + compressed + b"\nendstream\nendobj\n%%EOF"
    )


class PdfParserTests(unittest.TestCase):
    def test_parses_jane_doe_16_week_schedule_completely(self):
        plan = parse_training_plan_pdf(JANE_PDF.read_bytes())
        meta = plan["metadata"]
        self.assertEqual(meta["program"], "Ship Shape Marathon Training Schedule")
        self.assertEqual(meta["schedule_id"], "SchF101")
        self.assertEqual(meta["athlete"], "Jane Doe")
        self.assertEqual(meta["created_date"], "2026-09-01")
        self.assertEqual(meta["start_date"], "2026-09-28")
        self.assertEqual(meta["race_name"], "Houston Marathon")
        self.assertEqual(meta["race_date"], "2027-01-17")
        self.assertEqual(
            meta["equivalents"]["marathon"],
            {"time": "3:35:00", "pace_per_mile": "0:08:12"},
        )
        self.assertEqual(
            meta["equivalents"]["boston_qualifying"],
            {"time": "3:25:00", "pace_per_mile": "0:07:49"},
        )

        weeks = plan["weeks"]
        self.assertEqual(len(weeks), 16)
        self.assertEqual(weeks[0]["week_number"], 16)
        self.assertEqual(weeks[0]["week_of"], "2026-09-28")
        self.assertEqual(weeks[-1]["week_number"], 1)
        self.assertEqual(weeks[-1]["week_of"], "2027-01-11")

        # Week 16 Fartlek & Hill boilerplate stripped
        w16 = weeks[0]
        self.assertEqual(w16["days"]["Tuesday"]["type"], "Speed Work")
        self.assertEqual(w16["days"]["Tuesday"]["workout_mi"], 3.0)
        self.assertIsNone(w16["days"]["Tuesday"]["target_pace"])
        self.assertNotIn("Plus 1 mi warmup", w16["days"]["Tuesday"]["description"])
        self.assertEqual(w16["days"]["Thursday"]["type"], "Hill")

        # Week 14 Pyramid track ladder sums 400+800+1600+1600+800+400 = 5600m = 3.5 mi
        w14 = weeks[2]
        self.assertEqual(w14["days"]["Tuesday"]["workout_mi"], 3.5)
        self.assertEqual(w14["days"]["Tuesday"]["target_pace"], "07:06")

        # Week 13 6x1000m -> 3.75 mi
        w13 = weeks[3]
        self.assertEqual(w13["days"]["Tuesday"]["workout_mi"], 3.75)
        self.assertEqual(w13["days"]["Tuesday"]["target_pace"], "04:20/1000m")

        # Week 8 progressive long run
        w8 = weeks[8]
        self.assertEqual(w8["days"]["Saturday"]["distance_mi"], 20.0)
        self.assertEqual(w8["days"]["Saturday"]["target_pace"], "08:18 / 08:12")

        # Week 1 Marathon race
        w1 = weeks[-1]
        self.assertEqual(w1["days"]["Saturday"]["type"], "Race")
        self.assertEqual(w1["days"]["Saturday"]["distance_mi"], 26.2)
        self.assertEqual(w1["summary"], {"workout_miles": 30.7, "easy_miles": 10.0, "total_miles": 40.7})

    def test_parses_john_doe_16_week_schedule_completely(self):
        plan = parse_training_plan_pdf(JOHN_PDF.read_bytes())
        meta = plan["metadata"]
        self.assertEqual(meta["program"], "Ship Shape Marathon Training Schedule")
        self.assertEqual(meta["schedule_id"], "SchM204")
        self.assertEqual(meta["athlete"], "John Doe")
        self.assertEqual(meta["created_date"], "2026-10-12")
        self.assertEqual(meta["start_date"], "2026-10-26")
        self.assertEqual(meta["race_name"], "Austin Marathon")
        self.assertEqual(meta["race_date"], "2027-02-14")
        self.assertEqual(
            meta["equivalents"]["5K"],
            {"time": "0:24:00", "pace_per_mile": "0:07:43"},
        )
        self.assertEqual(
            meta["equivalents"]["marathon"],
            {"time": "3:55:00", "pace_per_mile": "0:08:58"},
        )

        weeks = plan["weeks"]
        self.assertEqual(len(weeks), 16)
        self.assertEqual(weeks[0]["week_number"], 16)
        self.assertEqual(weeks[0]["week_of"], "2026-10-26")
        self.assertEqual(weeks[-1]["week_number"], 1)
        self.assertEqual(weeks[-1]["week_of"], "2027-02-08")
        self.assertEqual(weeks[2]["days"]["Tuesday"]["target_pace"], "07:43")
        self.assertEqual(weeks[3]["days"]["Tuesday"]["target_pace"], "04:45/1000m")
        self.assertEqual(weeks[8]["days"]["Saturday"]["target_pace"], "09:05 / 08:58")
        self.assertEqual(weeks[-1]["days"]["Saturday"]["type"], "Race")
        self.assertEqual(weeks[-1]["days"]["Saturday"]["distance_mi"], 26.2)

    def test_parses_synthetic_pdf_and_year_rollover(self):
        plan = parse_training_plan_pdf(_build_synthetic_shipshape_pdf())
        meta = plan["metadata"]
        self.assertEqual(meta["athlete"], "Jane Doe")
        self.assertEqual(meta["schedule_id"], "SchX999")
        self.assertEqual(meta["race_name"], "Houston Marathon")
        self.assertEqual(meta["race_date"], "2027-01-10")
        self.assertEqual(meta["equivalents"]["5K"]["pace_per_mile"], "0:07:43")

        self.assertEqual(len(plan["weeks"]), 2)
        w2, w1 = plan["weeks"]
        self.assertEqual(w2["week_of"], "2026-12-28")
        self.assertEqual(w2["days"]["Tuesday"]["target_pace"], "07:43")
        self.assertEqual(w2["days"]["Saturday"]["target_pace"], "09:15 / 08:58")
        self.assertEqual(w1["week_of"], "2027-01-04")
        self.assertEqual(w1["days"]["Tuesday"]["workout_mi"], 2.0)
        self.assertEqual(w1["days"]["Tuesday"]["target_pace"], "01:45/400m")
        self.assertEqual(w1["days"]["Saturday"]["type"], "Race")

    def test_rejects_invalid_and_unstructured_pdfs(self):
        with self.assertRaises(ValueError):
            parse_training_plan_pdf(b"not a pdf")
        with self.assertRaises(ValueError):
            parse_training_plan_pdf(b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF")


if __name__ == "__main__":
    unittest.main()
