"""Message 3 (beat 7 and 7a): the Finish screen.

What these tests pin:

- the framing match math (framing.match_window): the crop is
  sized from the NEW video's face and the reference's face width
  share, positioned so the face lands at the reference's center
  shares; a missing reference or a missing face falls back to a
  centered crop with matched False, so no match line can be
  faked; a too-tight source says so; the per-shape nudge moves
  the predicted landing by exactly the nudge;
- the match report's plain line and percentages;
- the one ruler (framing.ruler_profile): the reference is
  learned with it, and a measured size miss corrects the crop
  by the measured ratio (framing.scale_window), landing the
  predicted share on the reference's;
- the export's frame: the shape's full frame for the source
  (16:9 at the source's own frame, 9:16 at 2160 tall), the
  correction loop's proxy frame (1080 high, same shape), and
  the loudness correction as a plain gain of exactly the miss;
- the Finish state: chosen versions (at least one always),
  per-shape nudges clamped to a tenth of the frame;
- the subtitle text: sentence lines in normal case from the
  plan's kept words;
- the cost line's honesty: a paid run shows about its recorded
  dollars, a zero-cost run says it ran on this computer, and no
  figure is ever invented;
- the HTTP surface: state, versions, the project file's save,
  Recent list, and reopen restoring the plan; the timeline
  exports parse back and carry the plan's pieces;
- the Studio page carries the Finish screen's pieces, including
  the CapCut row's exact words.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from talk_to_your_editor import costs, finish, framing, teaching  # noqa: E402
from talk_to_your_editor import timeline_export, webapp  # noqa: E402
from test_v18_actions import ActionsServerCase, apply_cut, write_job  # noqa: E402

REF = {
    "found": True,
    "face": {"center_x": 0.5, "center_y": 0.4, "width": 0.30},
}
NEW = {
    "found": True,
    "face": {"center_x": 0.5, "center_y": 0.4, "width": 0.15},
}


class MatchWindowTests(unittest.TestCase):
    def test_crop_sizes_from_the_new_face(self):
        # 16:9 source, 16:9 shape: crop width = 0.15 / 0.30 of
        # the source width; the face then takes 0.30 of the
        # output, centered where the reference has it.
        win = framing.match_window(REF, NEW, 3840, 2160, "16:9")
        self.assertTrue(win["matched"])
        self.assertAlmostEqual(win["width"], 0.5)
        self.assertAlmostEqual(win["predicted"]["width"], 0.30)
        self.assertAlmostEqual(win["predicted"]["center_x"], 0.5)
        self.assertAlmostEqual(win["predicted"]["center_y"], 0.4)

    def test_no_reference_is_a_centered_crop_and_no_match(self):
        win = framing.match_window(None, NEW, 3840, 2160, "9:16")
        self.assertFalse(win["matched"])
        self.assertEqual(win["notes"], ["no_reference"])
        self.assertIsNone(win["predicted"])
        self.assertAlmostEqual(win["x"], (1 - win["width"]) / 2)

    def test_no_face_in_new_is_a_centered_crop_and_no_match(self):
        win = framing.match_window(
            REF, {"found": False}, 3840, 2160, "9:16"
        )
        self.assertFalse(win["matched"])
        self.assertEqual(win["notes"], ["no_face_in_new"])

    def test_too_tight_source_says_so(self):
        # A 9:16 crop of a 16:9 source cannot zoom out far
        # enough when the face is already wide in the source.
        big = {
            "found": True,
            "face": {"center_x": 0.5, "center_y": 0.4, "width": 0.28},
        }
        win = framing.match_window(REF, big, 3840, 2160, "9:16")
        self.assertIn("too_tight", win["notes"])
        self.assertGreater(win["predicted"]["width"], 0.30)

    def test_nudge_moves_the_landing_by_the_nudge(self):
        base = framing.match_window(REF, NEW, 3840, 2160, "16:9")
        moved = framing.match_window(
            REF, NEW, 3840, 2160, "16:9", offset_x=0.05
        )
        self.assertAlmostEqual(
            moved["predicted"]["center_x"]
            - base["predicted"]["center_x"],
            -0.05,
        )


class KeptRangeTimesTests(unittest.TestCase):
    def test_times_land_inside_kept_ranges_only(self):
        ranges = [
            {"start": 0.0, "end": 10.0},
            {"start": 100.0, "end": 110.0},
        ]
        times = framing.kept_range_times(ranges, sample_count=4)
        self.assertEqual(len(times), 4)
        for t in times:
            self.assertTrue(
                0.0 <= t <= 10.0 or 100.0 <= t <= 110.0,
                f"sample time {t} fell in a cut stretch",
            )
        # Spread over the kept timeline: two in each range.
        self.assertEqual(
            sum(1 for t in times if t < 50), 2
        )

    def test_no_ranges_no_times(self):
        self.assertEqual(framing.kept_range_times([]), [])


class MatchReportTests(unittest.TestCase):
    def test_plain_line_and_percents(self):
        out = {
            "found": True,
            "face": {"center_x": 0.51, "center_y": 0.40, "width": 0.309},
        }
        report = framing.match_report(REF, out)
        self.assertTrue(report["ok"])
        self.assertAlmostEqual(report["position_off_pct"], 1.0)
        self.assertAlmostEqual(report["size_off_pct"], 3.0)
        self.assertIn("Face position within 1.0 percent", report["line"])

    def test_no_face_no_line(self):
        self.assertEqual(
            framing.match_report(REF, {"found": False})["line"], ""
        )
        self.assertEqual(
            framing.match_report(None, NEW)["line"], ""
        )


class FinishStateTests(unittest.TestCase):
    def setUp(self):
        self.session = Path(tempfile.mkdtemp(prefix="tye-finish-"))

    def test_defaults_are_both_versions(self):
        state = finish.load_state(self.session)
        self.assertEqual(state["versions"], ["9:16", "16:9"])
        self.assertEqual(state["offsets"]["9:16"], {"x": 0.0, "y": 0.0})

    def test_versions_keep_at_least_one(self):
        finish.set_versions(self.session, ["9:16"])
        self.assertEqual(
            finish.load_state(self.session)["versions"], ["9:16"]
        )
        finish.set_versions(self.session, [])
        self.assertEqual(
            finish.load_state(self.session)["versions"], ["9:16"]
        )

    def test_nudge_clamps_to_a_tenth_of_the_frame(self):
        for _ in range(20):
            finish.nudge_offset(self.session, "9:16", 0.02, 0.0)
        state = finish.load_state(self.session)
        self.assertAlmostEqual(state["offsets"]["9:16"]["x"], 0.10)
        finish.nudge_offset(self.session, "9:16", 0.0, 0.0, reset=True)
        self.assertEqual(
            finish.load_state(self.session)["offsets"]["9:16"],
            {"x": 0.0, "y": 0.0},
        )

    def test_finish_output_size_is_the_full_frame(self):
        # The export fills a full frame at the source's own
        # quality (Justin's rule): 16:9 at the source's own
        # frame, 9:16 at that same frame turned on its side,
        # full portrait 4K for his source. Never the crop's
        # own size, and never capped by the source's height.
        self.assertEqual(
            finish.finish_output_size("16:9", 3840, 2160),
            (3840, 2160),
        )
        self.assertEqual(
            finish.finish_output_size("9:16", 3840, 2160),
            (2160, 3840),
        )
        self.assertEqual(
            finish.finish_output_size("16:9", 1920, 1080),
            (1920, 1080),
        )
        self.assertEqual(
            finish.finish_output_size("9:16", 1920, 1080),
            (1080, 1920),
        )


class ProxyOutputSizeTests(unittest.TestCase):
    def test_proxy_is_1080_high_in_the_same_shape(self):
        # The face-size correction loop iterates at this size
        # and the delivered file is rendered once at full
        # frame (Justin's direction): 9:16 full portrait 4K
        # iterates at 608x1080, 16:9 4K at 1920x1080.
        self.assertEqual(
            finish.proxy_output_size(2160, 3840), (608, 1080)
        )
        self.assertEqual(
            finish.proxy_output_size(3840, 2160), (1920, 1080)
        )

    def test_proxy_never_exceeds_the_full_frame(self):
        # A full frame already 1080 high or smaller is its own
        # proxy, so the handler renders it once, not twice.
        self.assertEqual(
            finish.proxy_output_size(1920, 1080), (1920, 1080)
        )
        self.assertEqual(
            finish.proxy_output_size(1280, 720), (1280, 720)
        )

    def test_proxy_dimensions_are_even(self):
        w, h = finish.proxy_output_size(2160, 3840)
        self.assertEqual(w % 2, 0)
        self.assertEqual(h % 2, 0)


class ScaleWindowTests(unittest.TestCase):
    def test_scaling_lands_the_share_on_the_reference(self):
        win = framing.match_window(REF, NEW, 3840, 2160, "16:9")
        # The render measured the face at 0.24 against the
        # reference's 0.30: the crop scales by that ratio and
        # the predicted share lands on the reference's, with
        # the face center unmoved.
        fixed = framing.scale_window(
            win, REF, NEW, 3840, 2160, "16:9", 0.24
        )
        self.assertTrue(fixed["matched"])
        self.assertAlmostEqual(fixed["width"], win["width"] * 0.8)
        self.assertAlmostEqual(fixed["predicted"]["width"], 0.30)
        self.assertAlmostEqual(fixed["predicted"]["center_x"], 0.5)
        self.assertAlmostEqual(fixed["predicted"]["center_y"], 0.4)
        self.assertIn("size_corrected", fixed["notes"])

    def test_scaling_past_the_widest_crop_says_too_tight(self):
        win = framing.match_window(REF, NEW, 3840, 2160, "9:16")
        # A measured share three times the reference's would
        # need a crop three times wider than the plan's, wider
        # than any 9:16 crop of this source: the widest wins.
        fixed = framing.scale_window(
            win, REF, NEW, 3840, 2160, "9:16", 0.90
        )
        self.assertIn("too_tight", fixed["notes"])
        # The widest 9:16 crop of a 3840x2160 source is 1215
        # px wide, as a share of the source width.
        self.assertAlmostEqual(fixed["width"], 1215 / 3840)


class RulerTests(unittest.TestCase):
    def test_ruler_profile_reads_with_the_one_sample_count(self):
        seen = {}
        original = framing.profile_from_video

        def stub(video, detector=None, sample_count=None, times=None):
            seen["sample_count"] = sample_count
            seen["times"] = times
            return {"status": "ok", "found": False}

        framing.profile_from_video = stub
        try:
            framing.ruler_profile(Path("x.mp4"))
        finally:
            framing.profile_from_video = original
        self.assertEqual(
            seen["sample_count"], framing.RULER_SAMPLE_COUNT
        )

    def test_teaching_learns_the_reference_with_the_ruler(self):
        seen = {}
        original = framing.ruler_profile

        def stub(video, times=None, detector=None):
            seen["video"] = video
            return {
                "status": "ok",
                "found": True,
                "source_width": 640,
                "source_height": 360,
                "face": {
                    "center_x": 0.5, "center_y": 0.4,
                    "width": 0.36, "height": 0.6,
                },
                "frames_with_face": 21,
            }

        framing.ruler_profile = stub
        try:
            tmp = Path(tempfile.mkdtemp(prefix="tye-ruler-"))
            draft = teaching.framing_draft_for(
                tmp,
                tmp / "finished.mp4",
                kind="mine",
                basis="pair",
                source_note=teaching.PAIR_SOURCE_NOTE,
            )
        finally:
            framing.ruler_profile = original
        self.assertIsNotNone(draft)
        self.assertEqual(seen["video"], tmp / "finished.mp4")


class LoudnessCorrectionTests(unittest.TestCase):
    def test_correction_is_a_plain_gain_of_exactly_the_miss(self):
        self.assertEqual(
            finish._gain_correction_filter(0.84), "volume=0.84dB"
        )
        self.assertEqual(
            finish._gain_correction_filter(-0.5), "volume=-0.50dB"
        )
        # Nothing rides along that could add gain of its own:
        # the limiter that used to sit here overshot the
        # correction by half a LUFS.
        self.assertNotIn(
            "alimiter", finish._gain_correction_filter(1.2)
        )


WORDS = [
    {"word": "Today", "start": 0.0, "end": 0.4},
    {"word": "is", "start": 0.4, "end": 0.6},
    {"word": "day", "start": 0.6, "end": 0.9},
    {"word": "thirty.", "start": 0.9, "end": 1.4},
    {"word": "It", "start": 2.0, "end": 2.2},
    {"word": "is", "start": 2.2, "end": 2.4},
    {"word": "cold.", "start": 2.4, "end": 2.9},
]


class SubtitleTests(unittest.TestCase):
    def test_srt_sentence_lines_normal_case(self):
        job = Path(tempfile.mkdtemp(prefix="tye-finish-srt-"))
        write_job(job, [dict(w) for w in WORDS])
        cuts = json.loads((job / "cuts.json").read_text())
        srt = finish.srt_from_plan(job, cuts)
        self.assertIn("-->", srt)
        self.assertIn("Today is day thirty.", srt)
        self.assertIn("It is cold.", srt)


class CostLineTests(unittest.TestCase):
    def _config(self, tmp: Path):
        job = tmp / "job"
        write_job(job, [dict(w) for w in WORDS])
        return webapp.WebAppConfig(job, launch_home=tmp / "launch")

    def test_no_entries_says_nothing_recorded(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-finish-cost-"))
        line = webapp._finish_cost_line(self._config(tmp))
        self.assertIn("nothing recorded", line)

    def test_zero_cost_entries_say_on_this_computer(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-finish-cost-"))
        config = self._config(tmp)
        config.session_dir.mkdir(parents=True, exist_ok=True)
        costs.append_session_entries(
            config.session_dir,
            [{"feature": "retakes", "usd": 0.0, "pool": "local"}],
        )
        line = webapp._finish_cost_line(config)
        self.assertIn("on this computer", line)
        self.assertNotIn("$", line)

    def test_paid_entries_show_about_the_dollars(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-finish-cost-"))
        config = self._config(tmp)
        config.session_dir.mkdir(parents=True, exist_ok=True)
        costs.append_session_entries(
            config.session_dir,
            [{"feature": "direction", "usd": 0.19, "pool": "meta"}],
        )
        line = webapp._finish_cost_line(config)
        self.assertIn("about 19 cents", line)


class FinishHttpTests(ActionsServerCase):
    def test_state_shape_and_honest_notes(self):
        status, data = self.get_json("/api/finish/state")
        self.assertEqual(status, 200)
        self.assertEqual(data["versions"], ["9:16", "16:9"])
        self.assertIn("cost_line", data)
        shape = data["shapes"]["9:16"]
        self.assertIsNone(shape["reference"])
        self.assertEqual(shape["match_line"], "")
        self.assertTrue(
            any("No reference of this shape" in n for n in shape["notes"])
        )

    def test_versions_round_trip(self):
        status, data = self.post_json(
            "/api/finish/versions", {"versions": ["9:16"]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["versions"], ["9:16"])
        status, data = self.get_json("/api/finish/state")
        self.assertEqual(data["versions"], ["9:16"])
        self.assertFalse(data["shapes"]["16:9"]["chosen"])

    def test_project_save_recent_and_open_restores_the_plan(self):
        apply_cut(self.config, 0, 2)
        status, saved = self.post_json("/api/project/save", {})
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved["ok"])
        status, recent = self.get_json("/api/project/recent")
        self.assertEqual(len(recent["projects"]), 1)
        project_id = recent["projects"][0]["id"]
        # Change the plan, then reopen: the saved plan returns.
        apply_cut(self.config, 2, 4)
        self.assertEqual(self.word_status(3), "cut")
        status, opened = self.post_json(
            "/api/project/open", {"id": project_id}
        )
        self.assertEqual(status, 200, opened)
        self.assertTrue(opened["ok"])
        self.assertEqual(self.word_status(3), "kept")
        self.assertEqual(self.word_status(0), "cut")

    def test_project_file_downloads(self):
        self.post_json("/api/project/save", {})
        status, body = self.request("/api/project/file")
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["kind"], "tye-project")
        self.assertIn("plan", payload)
        self.assertIn("source_videos", payload)

    def test_render_before_an_edit_says_so(self):
        status, data = self.post_json(
            "/api/finish/render", {"aspect": "9:16"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("Make the edit first", data["error"])


class TimelineExportTests(unittest.TestCase):
    CUTS = {
        "source_duration_s": 10.0,
        "frame_quantization": {"fps": 30.0},
        "kept_ranges": [
            {"start": 0.0, "end": 4.0},
            {"start": 6.0, "end": 10.0},
        ],
    }

    def test_all_three_formats_carry_the_pieces(self):
        out = timeline_export.export_timeline(self.CUTS, "5 Before.MOV")
        self.assertEqual(set(out), {"fcpxml", "premiere", "edl"})
        fcpxml = out["fcpxml"][1]
        root = ET.fromstring(fcpxml.split("?>", 1)[-1].replace(
            "<!DOCTYPE fcpxml>", ""
        ))
        clips = root.findall(".//asset-clip")
        self.assertEqual(len(clips), 2)
        premiere = out["premiere"][1]
        proot = ET.fromstring(premiere.split("?>", 1)[-1].replace(
            "<!DOCTYPE xmeml>", ""
        ))
        items = proot.findall(".//clipitem")
        self.assertGreaterEqual(len(items), 2)
        edl = out["edl"][1]
        events = [
            line for line in edl.splitlines()
            if re.match(r"^\d{3}\s+\S+\s+V\s", line)
        ]
        self.assertEqual(len(events), 2)


class Mp4DimensionsTests(unittest.TestCase):
    """The summary's dimension reader must not load the video.

    It used to read the whole file into memory; on the Day 30
    4K original that was gigabytes per /api/job call and it
    OOM-killed the Message 3 proof server. The walk now reads
    box headers only, proven here on a sparse file far bigger
    than RAM-friendly test sizes, with the moov at the end.
    """

    def test_dimensions_from_a_sparse_huge_file(self):
        import struct

        def box(kind: bytes, payload: bytes) -> bytes:
            return struct.pack(">I", len(payload) + 8) + kind + payload

        tkhd = box(b"tkhd", b"\x00" * 80 + struct.pack(
            ">II", 3840 << 16, 2160 << 16
        ))
        moov = box(b"moov", box(b"trak", tkhd))
        ftyp = box(b"ftyp", b"isom" + b"\x00" * 12)
        tmp = Path(tempfile.mkdtemp(prefix="tye-dims-"))
        path = tmp / "big.mov"
        with path.open("wb") as fh:
            fh.write(ftyp)
            free_size = 3 * 1024 * 1024 * 1024
            fh.write(struct.pack(">I", free_size) + b"free")
            fh.truncate(len(ftyp) + free_size)
            fh.seek(len(ftyp) + free_size)
            fh.write(moov)
        self.assertEqual(webapp._mp4_dimensions(path), (3840, 2160))

    def test_non_mp4_is_none(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-dims-"))
        path = tmp / "nope.mp4"
        path.write_bytes(b"not a video at all")
        self.assertIsNone(webapp._mp4_dimensions(path))


class StudioFinishPageTests(unittest.TestCase):
    def test_studio_carries_the_finish_screen(self):
        html = (
            ROOT / "src/talk_to_your_editor/templates/studio.html"
        ).read_text(encoding="utf-8")
        for marker in (
            'id="screenFinish"',
            'id="finishTabs"',
            "CapCut can't open a timeline file. Download the "
            "finished video and bring it into CapCut.",
            # One Export in the app (0.27.3, chunk B item 9):
            # the Finish panel's own export button is gone;
            # what stands here opens the chat export instead.
            "Export in the chat",
            "Recent projects",
            'id="versionPick"',
            'id="tbExport"',
        ):
            self.assertIn(marker, html)
        # The old button words are gone (the CapCut row's
        # sentence, which the brief itself dictates, is the one
        # place "Download the finished video" still appears, as
        # part of that sentence and never as a button).
        self.assertNotIn("Download the finished edit", html)
        self.assertNotIn(">Download the finished video<", html)
        self.assertEqual(html.count("speechSynthesis"), 0)


if __name__ == "__main__":
    unittest.main()
