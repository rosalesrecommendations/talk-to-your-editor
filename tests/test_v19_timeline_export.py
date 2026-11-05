"""v0.19 Builder A: timeline export, the pure format writers.

The fixture is a four-piece plan at 30 fps, built to the v0.19
plan's first success criterion: at least three kept ranges, one
reordered piece (piece 2 sits earlier in the recording than
piece 1 but plays after it), one piece carrying a fade_in_s, and
one special insert whose donor audio lives somewhere else in the
recording entirely.

Piece layout (source frames at 30 fps, record frames cumulative):

- P1 [10.0, 15.0]            src 300-450   rec 0-150
- P2 [0.8, 5.7] fade 0.3s    src 24-171    rec 150-297
- P3 [20.6, 23.6]            src 618-708   rec 297-387
- P4 special [6.1667, 6.4667] donor audio [30.0, 30.3]
                             src 185-194   rec 387-396

Everything is offline; nothing is written to disk.
"""

from __future__ import annotations

import copy
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.timeline_export import export_timeline

FPS = 30


def make_cuts():
    return {
        "source_duration_s": 26.5,
        "frame_quantization": {"fps": 30.0, "ranges": 4, "repairs": 0},
        "kept_ranges": [
            {"start": 10.0, "end": 15.0},
            {"start": 0.8, "end": 5.7, "fade_in_s": 0.3},
            {"start": 20.6, "end": 23.6},
            {
                "start": 6.1667,
                "end": 6.4667,
                "audio_start": 30.0,
                "audio_end": 30.3,
                "insert_kind": "signature_breath_gasp",
                "video_source": "source_window",
            },
        ],
    }


EXPECTED = [  # (src_in, src_out, rec_in, rec_out) in frames
    (300, 450, 0, 150),
    (24, 171, 150, 297),
    (618, 708, 297, 387),
    (185, 194, 387, 396),
]


def frames_of(rational: str) -> int:
    """Parse an FCPXML rational time ('150/30s', '0s') back to frames."""
    assert rational.endswith("s")
    body = rational[:-1]
    if "/" in body:
        num, den = body.split("/")
        return round(int(num) / int(den) * FPS)
    return round(float(body) * FPS)


def tc_frames(tc: str) -> int:
    hh, mm, ss, ff = (int(part) for part in tc.split(":"))
    return ((hh * 60 + mm) * 60 + ss) * FPS + ff


class ExportShapeTests(unittest.TestCase):
    def test_three_formats_with_plain_filenames(self):
        out = export_timeline(make_cuts(), "IMG_2516.MOV")
        self.assertEqual(set(out), {"fcpxml", "premiere", "edl"})
        self.assertTrue(out["fcpxml"][0].endswith(".fcpxml"))
        self.assertTrue(out["premiere"][0].endswith(".xml"))
        self.assertTrue(out["edl"][0].endswith(".edl"))
        for key in out:
            self.assertIn("IMG_2516", out[key][0])

    def test_input_plan_is_not_changed(self):
        cuts = make_cuts()
        before = copy.deepcopy(cuts)
        export_timeline(cuts, "IMG_2516.MOV")
        self.assertEqual(cuts, before)


class FcpxmlTests(unittest.TestCase):
    def setUp(self):
        _name, self.text, self.notes = export_timeline(
            make_cuts(), "IMG_2516.MOV"
        )["fcpxml"]
        self.root = ET.fromstring(self.text)

    def clips(self):
        return self.root.findall(".//asset-clip")

    def test_every_range_once_in_output_order_gapless(self):
        clips = self.clips()
        self.assertEqual(len(clips), len(EXPECTED))
        for clip, (src_in, src_out, rec_in, rec_out) in zip(clips, EXPECTED):
            self.assertEqual(frames_of(clip.get("start")), src_in)
            self.assertEqual(
                frames_of(clip.get("start")) + frames_of(clip.get("duration")),
                src_out,
            )
            self.assertEqual(frames_of(clip.get("offset")), rec_in)
            self.assertEqual(
                frames_of(clip.get("offset")) + frames_of(clip.get("duration")),
                rec_out,
            )

    def test_fade_is_an_audio_transition_at_the_join(self):
        transitions = self.root.findall(".//transition")
        self.assertEqual(len(transitions), 1)
        transition = transitions[0]
        self.assertEqual(transition.get("name"), "Cross Fade")
        self.assertEqual(frames_of(transition.get("duration")), 9)
        # Centered on the join at record frame 150.
        offset = frames_of(transition.get("offset"))
        self.assertLessEqual(offset, 150)
        self.assertGreaterEqual(offset + 9, 150)

    def test_special_insert_is_noted_not_silent(self):
        joined = " ".join(self.notes)
        self.assertIn("signature_breath_gasp", joined)
        self.assertIn("30.00s", joined)

    def test_fcpxml_notes_do_not_claim_the_fade_is_missing(self):
        joined = " ".join(self.notes)
        self.assertNotIn("cannot carry", joined)


class PremiereXmlTests(unittest.TestCase):
    def setUp(self):
        _name, self.text, self.notes = export_timeline(
            make_cuts(), "IMG_2516.MOV"
        )["premiere"]
        self.root = ET.fromstring(self.text)

    def test_video_clipitems_match_the_plan(self):
        items = self.root.findall(".//video/track/clipitem")
        self.assertEqual(len(items), len(EXPECTED))
        for item, (src_in, src_out, rec_in, rec_out) in zip(items, EXPECTED):
            self.assertEqual(int(item.findtext("in")), src_in)
            self.assertEqual(int(item.findtext("out")), src_out)
            self.assertEqual(int(item.findtext("start")), rec_in)
            self.assertEqual(int(item.findtext("end")), rec_out)

    def test_audio_track_carries_the_fade_as_a_transition(self):
        transitions = self.root.findall(".//audio/track/transitionitem")
        self.assertEqual(len(transitions), 1)
        transition = transitions[0]
        start = int(transition.findtext("start"))
        end = int(transition.findtext("end"))
        self.assertEqual(end - start, 9)
        self.assertLessEqual(start, 150)
        self.assertGreaterEqual(end, 150)
        self.assertEqual(
            transition.findtext("./effect/effectid"), "Cross Fade"
        )


EDL_EVENT = re.compile(
    r"^(\d{3})\s+(\S+)\s+([VA])\s+C\s+"
    r"(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})\s+"
    r"(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})$"
)


class EdlTests(unittest.TestCase):
    def setUp(self):
        _name, self.text, self.notes = export_timeline(
            make_cuts(), "IMG_2516.MOV"
        )["edl"]
        self.events = []
        for line in self.text.splitlines():
            match = EDL_EVENT.match(line)
            if match:
                self.events.append(match.groups())

    def test_every_range_once_as_video_and_audio(self):
        video = [e for e in self.events if e[2] == "V"]
        audio = [e for e in self.events if e[2] == "A"]
        self.assertEqual(len(video), len(EXPECTED))
        self.assertEqual(len(audio), len(EXPECTED))

    def test_source_and_record_timecode(self):
        video = [e for e in self.events if e[2] == "V"]
        one_hour = 3600 * FPS
        for event, (src_in, src_out, rec_in, rec_out) in zip(video, EXPECTED):
            self.assertEqual(tc_frames(event[3]), src_in)
            self.assertEqual(tc_frames(event[4]), src_out)
            self.assertEqual(tc_frames(event[5]), one_hour + rec_in)
            self.assertEqual(tc_frames(event[6]), one_hour + rec_out)

    def test_fade_left_behind_is_noted(self):
        joined = " ".join(self.notes)
        self.assertIn("crossfade", joined)
        self.assertIn("EDL", joined)


class HonestyTests(unittest.TestCase):
    def test_degenerate_range_is_left_out_with_a_note(self):
        cuts = make_cuts()
        cuts["kept_ranges"].insert(1, {"start": 5.0, "end": 5.0})
        _name, text, notes = export_timeline(cuts, "IMG_2516.MOV")["fcpxml"]
        root = ET.fromstring(text)
        self.assertEqual(len(root.findall(".//asset-clip")), len(EXPECTED))
        self.assertIn("no length", " ".join(notes))

    def test_empty_plan_exports_empty_files_with_a_note(self):
        out = export_timeline({"kept_ranges": []}, "IMG_2516.MOV")
        for key in ("fcpxml", "premiere", "edl"):
            self.assertTrue(out[key][1])
            self.assertIn("no kept pieces", " ".join(out[key][2]))
        root = ET.fromstring(out["fcpxml"][1])
        self.assertEqual(root.findall(".//asset-clip"), [])

    def test_fps_falls_back_to_the_plan_then_to_30_with_a_note(self):
        cuts = make_cuts()
        del cuts["frame_quantization"]
        _name, _text, notes = export_timeline(cuts, "IMG_2516.MOV")["edl"]
        self.assertIn("assume 30", " ".join(notes))
        _name, _text, notes = export_timeline(
            cuts, "IMG_2516.MOV", fps=25.0
        )["edl"]
        self.assertNotIn("assume 30", " ".join(notes))

    def test_first_piece_fade_is_noted_not_dropped_silently(self):
        cuts = make_cuts()
        cuts["kept_ranges"][0]["fade_in_s"] = 0.2
        _name, text, notes = export_timeline(cuts, "IMG_2516.MOV")["fcpxml"]
        self.assertIn("first piece", " ".join(notes))
        root = ET.fromstring(text)
        self.assertEqual(len(root.findall(".//transition")), 1)


if __name__ == "__main__":
    unittest.main()
