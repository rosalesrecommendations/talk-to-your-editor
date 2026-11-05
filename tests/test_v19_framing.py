"""Tests for v0.19 chunk 4: framing (crop and face position).

The profile and crop math is pure and covered directly. Detection
is covered at its decode seam with synthetic model outputs (numpy
is a test-time tool here; the app itself treats numpy and
onnxruntime as optional and says so plainly when they are
missing). The one true end-to-end detection test runs only when
TYE_TEST_FACE_VIDEO names a real video with a face and
onnxruntime is importable: the repo's own test materials hold no
face footage, so no fixture is invented for it.

Everything is offline. No test makes a live call or spends
anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import framing, intake_build, teaching
from talk_to_your_editor import transcribe as speech

FFMPEG = shutil.which("ffmpeg")


def box(x1, y1, x2, y2, score=0.9):
    return (x1, y1, x2, y2, score)


class BuildProfileTests(unittest.TestCase):
    def test_median_face_ignores_faceless_frames(self):
        frames = [
            [box(100, 100, 300, 300)],   # center .2/.2... 1000px frame
            [],
            [box(120, 100, 320, 300)],
            [box(110, 90, 310, 290)],
        ]
        profile = framing.build_profile(1000, 1000, frames)
        self.assertTrue(profile["found"])
        self.assertEqual(profile["frames_sampled"], 4)
        self.assertEqual(profile["frames_with_face"], 3)
        face = profile["face"]
        self.assertAlmostEqual(face["center_x"], 0.21)
        self.assertAlmostEqual(face["center_y"], 0.20)
        self.assertAlmostEqual(face["width"], 0.20)
        self.assertAlmostEqual(profile["zoom"], face["width"])

    def test_biggest_face_in_a_frame_wins(self):
        frames = [[box(0, 0, 50, 50), box(200, 200, 500, 500)]]
        profile = framing.build_profile(1000, 1000, frames)
        self.assertAlmostEqual(profile["face"]["width"], 0.30)
        self.assertAlmostEqual(profile["face"]["center_x"], 0.35)

    def test_no_face_anywhere_is_a_plain_no(self):
        profile = framing.build_profile(640, 480, [[], [], []])
        self.assertFalse(profile["found"])
        self.assertNotIn("face", profile)
        self.assertEqual(profile["frames_with_face"], 0)


class CropWindowTests(unittest.TestCase):
    PROFILE = {
        "found": True,
        "face": {"center_x": 0.5, "center_y": 0.4,
                 "width": 0.25, "height": 0.45},
    }

    def test_tall_from_wide_source(self):
        window = framing.crop_window(self.PROFILE, 1920, 1080, "9:16")
        self.assertAlmostEqual(window["height"], 1.0)
        self.assertAlmostEqual(
            window["width"], (9 / 16) / (16 / 9), places=6
        )
        # Face center lands at the reference share across.
        self.assertAlmostEqual(window["face_center_x"], 0.5)
        self.assertAlmostEqual(window["x"], 0.5 - window["width"] / 2)

    def test_wide_from_tall_source(self):
        window = framing.crop_window(self.PROFILE, 1080, 1920, "16:9")
        self.assertAlmostEqual(window["width"], 1.0)
        self.assertAlmostEqual(
            window["height"], (9 / 16) / (16 / 9), places=6
        )
        self.assertAlmostEqual(window["face_center_x"], 0.5)

    def test_face_keeps_its_share_of_the_frame(self):
        profile = {
            "found": True,
            "face": {"center_x": 0.62, "center_y": 0.4,
                     "width": 0.25, "height": 0.45},
        }
        window = framing.crop_window(profile, 1920, 1080, "9:16")
        self.assertAlmostEqual(window["face_center_x"], 0.62, places=6)
        window = framing.crop_window(profile, 1080, 1920, "16:9")
        self.assertAlmostEqual(window["face_center_y"], 0.4, places=6)

    def test_face_near_left_edge_keeps_its_share(self):
        profile = {
            "found": True,
            "face": {"center_x": 0.05, "center_y": 0.4,
                     "width": 0.25, "height": 0.45},
        }
        window = framing.crop_window(profile, 1920, 1080, "9:16")
        self.assertAlmostEqual(
            window["x"], 0.05 * (1.0 - window["width"]), places=6
        )
        self.assertAlmostEqual(window["face_center_x"], 0.05, places=6)
        self.assertGreaterEqual(window["x"], 0.0)

    def test_face_near_right_edge_keeps_its_share(self):
        profile = {
            "found": True,
            "face": {"center_x": 0.97, "center_y": 0.4,
                     "width": 0.25, "height": 0.45},
        }
        window = framing.crop_window(profile, 1920, 1080, "9:16")
        self.assertAlmostEqual(window["face_center_x"], 0.97, places=6)
        self.assertLessEqual(
            window["x"] + window["width"], 1.0 + 1e-9
        )

    def test_no_profile_centers_the_window(self):
        window = framing.crop_window(None, 1920, 1080, "9:16")
        self.assertAlmostEqual(
            window["x"], (1.0 - window["width"]) / 2, places=6
        )
        self.assertNotIn("face_center_x", window)


class CropPixelsTests(unittest.TestCase):
    def test_even_and_inside_the_source(self):
        window = {"x": 0.341, "y": 0.0, "width": 0.316, "height": 1.0}
        x, y, w, h = framing.crop_pixels(window, 1920, 1080)
        for value in (x, y, w, h):
            self.assertEqual(value % 2, 0)
        self.assertLessEqual(x + w, 1920)
        self.assertLessEqual(y + h, 1080)
        self.assertGreaterEqual(w, 2)
        self.assertGreaterEqual(h, 2)


class FramingSentenceTests(unittest.TestCase):
    def test_center_close_up(self):
        profile = {
            "found": True,
            "face": {"center_x": 0.5, "center_y": 0.45,
                     "width": 0.36, "height": 0.6},
        }
        self.assertEqual(
            framing.framing_sentence(profile),
            "Your face sits center frame, fairly close up.",
        )

    def test_left_high_medium(self):
        profile = {
            "found": True,
            "face": {"center_x": 0.3, "center_y": 0.3,
                     "width": 0.25, "height": 0.4},
        }
        self.assertEqual(
            framing.framing_sentence(profile),
            "Your face sits left of center and high in the "
            "frame, a medium shot.",
        )

    def test_no_face_no_sentence(self):
        self.assertEqual(framing.framing_sentence({"found": False}), "")
        self.assertEqual(framing.framing_sentence(None), "")


class DecodeTests(unittest.TestCase):
    """The YuNet decode, driven with synthetic outputs."""

    def setUp(self):
        try:
            import numpy
        except ImportError:
            self.skipTest("numpy not available")
        self.np = numpy

    def outputs(self):
        np = self.np
        out = {}
        for stride, count in ((8, 6400), (16, 1600), (32, 400)):
            out[f"cls_{stride}"] = np.zeros((1, count, 1), np.float32)
            out[f"obj_{stride}"] = np.zeros((1, count, 1), np.float32)
            out[f"bbox_{stride}"] = np.zeros((1, count, 4), np.float32)
            out[f"kps_{stride}"] = np.zeros((1, count, 10), np.float32)
        return out

    def test_one_location_decodes_to_its_cell(self):
        out = self.outputs()
        grid = 20  # stride 32
        index = 6 * grid + 10  # cell (10, 6), center (336, 208)
        out["cls_32"][0, index, 0] = 0.9
        out["obj_32"][0, index, 0] = 0.9
        found = framing.decode_detections(out, self.np)
        self.assertEqual(len(found), 1)
        x1, y1, x2, y2, score = found[0]
        # Offsets zero, log size zero: center is the cell center,
        # size is one stride.
        self.assertAlmostEqual((x1 + x2) / 2, 336.0)
        self.assertAlmostEqual((y1 + y2) / 2, 208.0)
        self.assertAlmostEqual(x2 - x1, 32.0)
        self.assertAlmostEqual(score, 0.81, places=6)

    def test_below_threshold_and_nms(self):
        out = self.outputs()
        grid = 20
        strong = 6 * grid + 10
        weak = 6 * grid + 11
        for index, cls in ((strong, 0.95), (weak, 0.7)):
            out["cls_32"][0, index, 0] = cls
            out["obj_32"][0, index, 0] = 0.95
        # The weak one sits one cell over with a huge overlap in
        # log size: NMS keeps only the stronger.
        out["bbox_32"][0, weak] = [0.0, 0.0, 2.0, 2.0]
        out["bbox_32"][0, strong] = [1.0, 0.0, 2.0, 2.0]
        faint = 3 * grid + 3
        out["cls_32"][0, faint, 0] = 0.5  # 0.5 * 0.95 < 0.6
        out["obj_32"][0, faint, 0] = 0.95
        found = framing.decode_detections(out, self.np)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0][4], 0.95 * 0.95, places=6)


class ProfileFromVideoTests(unittest.TestCase):
    def make_video(self, tmp: Path) -> Path:
        source = tmp / "clip.mp4"
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30",
                "-t", "2", str(source),
            ],
            check=True,
        )
        return source

    @unittest.skipUnless(FFMPEG, "ffmpeg not available")
    def test_stub_detector_profile_scales_back_to_source(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-framing-"))
        try:
            source = self.make_video(tmp)

            def detector(rgb, width, height):
                self.assertEqual((width, height), (640, 640))
                return [box(256, 180, 384, 300)]

            profile = framing.profile_from_video(
                source, detector=detector, sample_count=3
            )
            self.assertEqual(profile["status"], "ok")
            self.assertEqual(profile["source_width"], 640)
            self.assertEqual(profile["source_height"], 360)
            face = profile["face"]
            self.assertAlmostEqual(face["center_x"], 0.5)
            self.assertAlmostEqual(face["width"], 0.2)
            # y scales by 360/640: box 180..300 -> 101.25..168.75
            self.assertAlmostEqual(face["center_y"], 0.375)
            self.assertAlmostEqual(face["height"], 0.1875)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless(FFMPEG, "ffmpeg not available")
    def test_no_face_found_is_status_no_face(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-framing-"))
        try:
            source = self.make_video(tmp)
            profile = framing.profile_from_video(
                source, detector=lambda rgb, w, h: [], sample_count=2
            )
            self.assertEqual(profile["status"], "no_face")
            self.assertFalse(profile["found"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TeachingFramingTests(unittest.TestCase):
    OK_PROFILE = {
        "status": "ok",
        "found": True,
        "source_width": 640,
        "source_height": 360,
        "face": {"center_x": 0.5, "center_y": 0.4,
                 "width": 0.36, "height": 0.6},
        "frames_with_face": 6,
    }

    def test_draft_carries_the_sentence_and_saves_the_profile(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-framing-"))
        try:
            draft = teaching.framing_draft_for(
                tmp,
                tmp / "finished.mp4",
                kind="mine",
                basis="pair",
                source_note=teaching.PAIR_SOURCE_NOTE,
                profile_fn=lambda path: dict(self.OK_PROFILE),
            )
            self.assertIsNotNone(draft)
            self.assertEqual(
                draft["statement"],
                "Your face sits center frame, fairly close up.",
            )
            self.assertIn("36%", draft["reason"])
            saved = teaching.load_framing_profile(tmp)
            self.assertIsNotNone(saved)
            self.assertTrue(saved["found"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_face_saves_nothing_and_drafts_nothing(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-framing-"))
        try:
            draft = teaching.framing_draft_for(
                tmp,
                tmp / "finished.mp4",
                kind="style",
                basis="finished_only",
                source_note=teaching.FINISHED_SOURCE_NOTE,
                profile_fn=lambda path: {
                    "status": "no_face", "found": False
                },
            )
            self.assertIsNone(draft)
            self.assertIsNone(teaching.load_framing_profile(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SpliceCropTests(unittest.TestCase):
    RANGES = [
        {"start": 0.0, "end": 1.0},
        {"start": 2.0, "end": 3.0},
    ]

    def test_default_graph_has_no_crop(self):
        plain = intake_build.splice_filter_graph(self.RANGES, True, 0.0)
        explicit = intake_build.splice_filter_graph(
            self.RANGES, True, 0.0, None
        )
        self.assertEqual(plain, explicit)
        self.assertNotIn("crop=", plain)

    def test_crop_graph_crops_and_scales_after_the_join(self):
        crop = (165, 0, 270, 480, 270, 480)
        graph = intake_build.splice_filter_graph(
            self.RANGES, True, 0.0, crop
        )
        self.assertIn("crop=270:480:165:0,scale=270:480[outv]", graph)
        self.assertEqual(graph.count("[outv]"), 1)

    @unittest.skipUnless(FFMPEG, "ffmpeg not available")
    def test_render_with_crop_changes_the_picture_size(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-framing-"))
        try:
            source = tmp / "src.mp4"
            subprocess.run(
                [
                    FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
                    "-f", "lavfi", "-i", "sine=frequency=440",
                    "-t", "2", str(source),
                ],
                check=True,
            )
            cuts = {"kept_ranges": [{"start": 0.0, "end": 1.5}]}
            window = framing.crop_window(None, 320, 240, "9:16")
            x, y, w, h = framing.crop_pixels(window, 320, 240)
            out_w, out_h = framing.output_size(w, h, "9:16")
            out = tmp / "out.mp4"
            intake_build.render_splice(
                source, cuts, out, crop=(x, y, w, h, out_w, out_h)
            )
            info = speech.probe_media(out)
            self.assertEqual(info["width"], out_w)
            self.assertEqual(info["height"], out_h)
            # Even-pixel rounding keeps the shape within a
            # percent or two of 9:16, not exact at small sizes.
            self.assertAlmostEqual(out_w / out_h, 9 / 16, delta=0.02)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RealDetectionTests(unittest.TestCase):
    """The real model on a real face video, when one is provided.

    The repo's test materials hold no face footage, so this runs
    only with TYE_TEST_FACE_VIDEO set and onnxruntime importable
    (the kit's speech extra carries it).
    """

    @unittest.skipUnless(
        os.environ.get("TYE_TEST_FACE_VIDEO"),
        "no face test video provided (TYE_TEST_FACE_VIDEO)",
    )
    def test_profile_from_a_real_face_video(self):
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            self.skipTest("onnxruntime not available")
        ok, why = framing.detection_status()
        self.assertTrue(ok, why)
        profile = framing.profile_from_video(
            Path(os.environ["TYE_TEST_FACE_VIDEO"])
        )
        self.assertEqual(profile["status"], "ok")
        self.assertTrue(profile["found"])
        self.assertGreater(profile["face"]["width"], 0.05)
        self.assertLess(profile["face"]["width"], 0.9)


if __name__ == "__main__":
    unittest.main()
