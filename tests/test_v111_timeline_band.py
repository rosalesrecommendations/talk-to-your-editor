"""v0.11.1, item 4: the timeline is a full width band.

From Justin's Oct 28, 2026 evening test of 0.11 and the Descript
screenshot he sent with it (FIX-ITEMS.md, item 4): the thin strip
under the video in the left column becomes a primary band across
the bottom of the review screen, under the video, transcript, and
comments row. Inside the band, in the model's order: the transport
controls and the current/total clock, a time ruler with second
marks, the segments strip, a captions row when captions are on,
and the audio waveform under it all, with one playhead line
running through every row.

Pinned here:

- the band markup sits after the columns row, spans the full
  width, and holds the ruler, the segments strip, the captions
  row hook, and the waveform canvas;
- the transport row, the clock, and the Prev/Next change controls
  are in the band, not in the video column, and the video column
  keeps the video and its own small controls;
- the band height drags by its top edge, is remembered in the
  same layout store as the panel arrangement under its own key
  (bandHeight), stops at a sane minimum, and Reset layout puts
  it back;
- clicking the band seeks through the same path the strip has
  always used, and the waveform row seeks the same way;
- the server sends waveform peaks measured from the job's own
  sound (shape, range, and source: loud and quiet slices land
  where the sound is loud and quiet, and a special insert's span
  stays unmeasured), and caption cues grouped by the captions
  module when the job's caption file exists, and nothing when
  it does not.

Everything is offline; no provider is called.
"""

from __future__ import annotations

import array
import json
import shutil
import sys
import tempfile
import unittest
import wave
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import webapp  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig  # noqa: E402

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def between(start_marker: str, end_marker: str) -> str:
    start = TEMPLATE.index(start_marker)
    end = TEMPLATE.index(end_marker, start)
    return TEMPLATE[start:end]


VOID_TAGS = {
    "meta", "link", "br", "hr", "img", "input", "source",
    "wbr", "col", "area", "base", "embed", "track",
}


class MarkupTree(HTMLParser):
    """Each element's parent, over the page markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list = []
        self.parents: dict = {}

    def _record(self, tag, attrs):
        el_id = dict(attrs).get("id")
        if el_id:
            self.parents[el_id] = (
                dict(self.stack[-1][1]).get("id") if self.stack else None
            ) or (self.stack[-1][0] if self.stack else None)
        return el_id

    def handle_starttag(self, tag, attrs):
        self._record(tag, attrs)
        if tag not in VOID_TAGS:
            self.stack.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self._record(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def parsed_tree() -> MarkupTree:
    tree = MarkupTree()
    tree.feed(TEMPLATE)
    return tree


class BandMarkupTests(unittest.TestCase):
    """The band sits after the columns row and spans full width."""

    def test_band_sits_after_the_columns_row(self):
        self.assertLess(
            TEMPLATE.index('id="reviewSplit"'),
            TEMPLATE.index('id="timelineBand"'),
        )
        self.assertLess(
            TEMPLATE.index('id="timelineBand"'),
            TEMPLATE.index("</main>"),
        )
        tree = parsed_tree()
        # A direct child of the page column, like the split, so
        # the fitted frame sizes it with the rest of the screen.
        self.assertEqual(tree.parents["timelineBand"], "main")
        self.assertEqual(tree.parents["reviewSplit"], "main")

    def test_band_spans_the_full_width(self):
        band_css = between(".timeline-band {", "}")
        self.assertIn("width: 100%", band_css)
        # The height is clamped: a stored or dragged height can
        # never push the band past the window or below its
        # minimum. The minimum (156px) is the smallest height at
        # which every row inside the band still fits inside it.
        self.assertIn("clamp(156px, var(--band-height, 176px), 360px)",
                      band_css)

    def test_band_rows_follow_the_model_order(self):
        band = between('id="timelineBand"', "</main>")
        order = [
            band.index('id="fmtRow"'),
            band.index('id="tlRuler"'),
            band.index('id="tlTrack"'),
            band.index('id="tlCaptionsRow"'),
            band.index('id="tlWaveCanvas"'),
        ]
        self.assertEqual(order, sorted(order))

    def test_ruler_segments_captions_and_waveform_hooks_are_in_the_band(self):
        band = between('id="timelineBand"', "</main>")
        for marker in (
            'id="tlRuler"',
            'id="tlTrack"',
            'id="tlBlocks"',
            'id="tlDots"',
            'id="tlPlayhead"',
            'id="tlCaptionsRow" hidden',
            'id="tlWaveRow"',
            'id="tlWaveCanvas"',
            'id="tlCaption"',
        ):
            self.assertIn(marker, band)

    def test_ruler_draws_second_marks(self):
        self.assertIn("tl-second-tick", TEMPLATE)
        self.assertIn("for (var s = 0; s <= d + 0.001; s += 1)", TEMPLATE)
        self.assertIn("function renderRuler()", TEMPLATE)

    def test_one_playhead_runs_through_every_row(self):
        # The playhead, the hover line, and the dots live in the
        # one stack that now holds the ruler, the segments, the
        # captions row, and the waveform, so the line the CSS
        # stretches from the stack's top to its bottom crosses
        # every row. Playing still moves it through updatePlayhead.
        stack = between('id="tlStack"', 'id="tlCaption"')
        for marker in (
            'id="tlRuler"', 'id="tlBlocks"', 'id="tlDots"',
            'id="tlCaptionsRow"', 'id="tlWaveCanvas"',
            'id="tlPlayhead"',
        ):
            self.assertIn(marker, stack)
        self.assertIn("function updatePlayhead()", TEMPLATE)
        self.assertIn("tlPlayhead.style.left = tlInsetLeft(frac, 6);",
                      TEMPLATE)


class BandTransportTests(unittest.TestCase):
    """Transport, clock, and change controls live on the band."""

    def test_transport_and_clock_are_in_the_band_not_the_column(self):
        band = between('id="timelineBand"', "</main>")
        left = between('class="review-left"', 'id="paneDivider"')
        for marker in (
            'id="fmtRow"',
            'id="clock">0:00 / 0:00<',
            'id="prevChangeBtn"',
            'id="nextChangeBtn"',
            'id="goStartBtn"',
            'id="back5"',
            'id="playPauseBtn"',
            'id="fwd5"',
            'id="goEndBtn"',
        ):
            self.assertIn(marker, band)
            self.assertNotIn(marker, left)

    def test_change_markers_and_dots_still_render_on_the_band(self):
        self.assertIn("function renderDots()", TEMPLATE)
        self.assertIn("function updateChangeButtons()", TEMPLATE)
        self.assertIn('id="prevChangeSlot" hidden', TEMPLATE)
        self.assertIn('id="nextChangeSlot" hidden', TEMPLATE)

    def test_video_column_keeps_the_video_and_its_own_controls(self):
        left = between('class="review-left"', 'id="paneDivider"')
        for marker in (
            'id="vid"',
            'id="swapSidesBtn"',
            'id="videoExpandBtn"',
            'id="videoHideBtn"',
            'id="makeEditCard"',
        ):
            self.assertIn(marker, left)
        # No hole where the strip was: nothing in the column waits
        # on the timeline or the transport any more.
        self.assertNotIn('id="tlbar"', left)


class BandHeightTests(unittest.TestCase):
    """The top edge drags; the height is remembered; Reset restores."""

    def test_height_is_stored_in_the_layout_store_under_its_own_key(self):
        self.assertIn("bandHeight: null", TEMPLATE)
        self.assertIn("parsedLayout.bandHeight", TEMPLATE)
        self.assertIn(
            "bandHeight", between("function fitRowBand()", "function clearExpandedPanel")
        )
        self.assertIn('"tye-layout"', TEMPLATE)

    def test_drag_is_wired_to_the_top_edge_with_a_minimum(self):
        self.assertIn('id="tlBandHandle"', TEMPLATE)
        self.assertIn('aria-orientation="horizontal"', TEMPLATE)
        self.assertIn(
            'tlBandHandle.addEventListener("pointerdown"', TEMPLATE)
        self.assertIn("function setBandHeight(px)", TEMPLATE)
        # The same limits in the drag and in the band CSS.
        self.assertIn("Math.min(360, Math.max(156, Math.round(px)))",
                      TEMPLATE)
        self.assertIn("clamp(156px, var(--band-height, 176px), 360px)",
                      TEMPLATE)
        self.assertIn('bandEl.style.setProperty("--band-height"',
                      TEMPLATE)

    def test_reset_layout_restores_the_band_height(self):
        reset = between(
            'resetLayoutBtn.addEventListener("click"',
            "// -- panel arrangement",
        )
        self.assertIn('removeItem("tye-layout")', reset)
        self.assertIn("bandHeight: null", reset)
        self.assertIn("applyLayout();", reset)


class BandSeekTests(unittest.TestCase):
    """Clicking the band seeks through the strip's own path."""

    def test_clicking_the_band_seeks(self):
        self.assertIn('tlTrack.addEventListener("pointerdown"', TEMPLATE)
        self.assertIn("function tlSeekEvent(e)", TEMPLATE)
        seek = between("function tlSeekEvent(e)", "function tlHoverEvent")
        self.assertIn("editedToFileTime(et)", seek)
        self.assertIn("updatePlayhead();", seek)

    def test_clicking_the_waveform_row_seeks_the_same_way(self):
        wave = between(
            'tlWaveRow.addEventListener("pointerdown"',
            'tlTrack.addEventListener("pointerdown"',
        )
        self.assertIn("tlSeekEvent(e);", wave)

    def test_clicking_a_caption_seeks_to_its_cue(self):
        row = between("function renderCaptionRow()", "function renderWaveform()")
        self.assertIn("seekToEdited(cue.start);", row)

    def test_clicking_a_word_still_seeks(self):
        # The transcript's own seek path is untouched by the move.
        self.assertIn("function seekToEdited(et, fmt)", TEMPLATE)
        self.assertIn("seekToEdited(", TEMPLATE)


class WaveformPeaksTests(unittest.TestCase):
    """Server: peaks derived from the audio, count and range sane."""

    def segments(self):
        return [
            {"duration": 1.0, "special": False,
             "range": {"start": 0.0, "end": 1.0}},
            {"duration": 1.0, "special": False,
             "range": {"start": 1.0, "end": 2.0}},
            {"duration": 1.0, "special": False,
             "range": {"start": 2.0, "end": 3.0}},
        ]

    def test_peaks_shape_and_range(self):
        samples = array.array(
            "f",
            [0.0] * 16000 + [0.5] * 16000 + [0.0] * 16000,
        )
        peaks = webapp.waveform_peaks_for_samples(
            samples, self.segments(), count=6
        )
        self.assertEqual(len(peaks), 6)
        for peak in peaks:
            self.assertGreaterEqual(peak, 0.0)
            self.assertLessEqual(peak, 1.0)
        # The loud middle second is the peak; the quiet ends sit low.
        self.assertEqual(peaks[2], 1.0)
        self.assertEqual(peaks[3], 1.0)
        self.assertEqual(peaks[0], 0.0)
        self.assertEqual(peaks[5], 0.0)

    def test_default_count_is_the_band_count(self):
        samples = array.array("f", [0.25] * 16000)
        peaks = webapp.waveform_peaks_for_samples(
            samples, self.segments()[:1]
        )
        self.assertEqual(len(peaks), webapp.WAVEFORM_PEAK_COUNT)
        self.assertEqual(max(peaks), 1.0)

    def test_silence_and_no_input_draw_nothing(self):
        silent = array.array("f", [0.0] * 48000)
        self.assertEqual(
            webapp.waveform_peaks_for_samples(silent, self.segments()),
            [],
        )
        self.assertEqual(
            webapp.waveform_peaks_for_samples(
                array.array("f"), self.segments()
            ),
            [],
        )
        self.assertEqual(
            webapp.waveform_peaks_for_samples(
                array.array("f", [0.5] * 16000), []
            ),
            [],
        )

    def test_special_insert_span_stays_unmeasured(self):
        segments = [
            {"duration": 1.0, "special": False,
             "range": {"start": 0.0, "end": 1.0}},
            {"duration": 1.0, "special": True,
             "range": {"start": 1.0, "end": 2.0}},
            {"duration": 1.0, "special": False,
             "range": {"start": 2.0, "end": 3.0}},
        ]
        samples = array.array("f", [0.5] * 48000)
        peaks = webapp.waveform_peaks_for_samples(
            samples, segments, count=6
        )
        self.assertEqual(peaks[0], 1.0)
        # The insert's own span carries no measured peak, even
        # though the recording is loud at its donor times.
        self.assertEqual(peaks[2], 0.0)
        self.assertEqual(peaks[3], 0.0)
        self.assertEqual(peaks[5], 1.0)


def write_wav(path: Path, seconds_loud: list) -> None:
    """A 16 kHz mono WAV: 0.5 level where loud, silence elsewhere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    import struct

    for loud in seconds_loud:
        value = 16000 if loud else 0
        frames += struct.pack("<h", value) * 16000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(bytes(frames))


class JobSummaryBandDataTests(unittest.TestCase):
    """The job summary carries the band's peaks and caption cues."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v111-band-"))
        self.job = self.tmp / "job"
        self.job.mkdir()
        transcript = {
            "engine": "fixture",
            "duration": 3.0,
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "there", "start": 0.5, "end": 0.9},
                {"word": "um", "start": 1.1, "end": 1.4},
                {"word": "friend.", "start": 1.6, "end": 2.0},
            ],
        }
        cuts = {
            "source_duration_s": 3.0,
            "kept_ranges": [{"start": 0.0, "end": 3.0}],
        }
        (self.job / "transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )
        (self.job / "cuts.json").write_text(
            json.dumps(cuts), encoding="utf-8"
        )
        self.config = WebAppConfig(self.job)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_audio_and_no_captions_send_empty_lists(self):
        summary = webapp._job_summary(self.config)
        self.assertEqual(summary["waveform_peaks"], [])
        self.assertEqual(summary["caption_cues"], [])
        self.assertEqual(
            webapp._no_job_summary(self.config)["waveform_peaks"], []
        )

    def test_peaks_come_from_the_jobs_own_sound(self):
        write_wav(
            self.job / "intake-work" / "audio.wav",
            [True, False, True],
        )
        summary = webapp._job_summary(self.config)
        peaks = summary["waveform_peaks"]
        self.assertEqual(len(peaks), webapp.WAVEFORM_PEAK_COUNT)
        self.assertTrue(all(0.0 <= p <= 1.0 for p in peaks))
        third = len(peaks) // 3
        self.assertEqual(max(peaks[:third]), 1.0)
        self.assertEqual(max(peaks[third:2 * third]), 0.0)
        self.assertEqual(max(peaks[2 * third:]), 1.0)

    def test_caption_cues_ride_along_when_the_caption_file_exists(self):
        session = self.job / "tye-session"
        session.mkdir(parents=True, exist_ok=True)
        (session / "clean-captions.vtt").write_text(
            "WEBVTT\n", encoding="utf-8"
        )
        summary = webapp._job_summary(self.config)
        cues = summary["caption_cues"]
        self.assertTrue(cues)
        for cue in cues:
            self.assertLessEqual(cue["start"], cue["end"])
            self.assertIn("text", cue)
        # The cues are the caption file's own grouping: the kept
        # words on the edited clock, fillers left out of the text.
        joined = " ".join(cue["text"] for cue in cues)
        self.assertIn("Hello", joined)
        self.assertIn("friend.", joined)
        self.assertNotIn(" um ", " " + joined + " ")


class CaptionsRowPageTests(unittest.TestCase):
    """The page shows the captions row only from what it is sent."""

    def test_row_is_hidden_without_captions_or_cues(self):
        row = between("function renderCaptionRow()", "function renderWaveform()")
        self.assertIn("if (!state.captionsAvailable || !captionCues.length",
                      row)
        self.assertIn("tlCaptionsRow.hidden = true;", row)
        self.assertIn("tlCaptionsRow.hidden = false;", row)

    def test_page_reads_the_band_data_from_the_job_summary(self):
        self.assertIn("waveformPeaks = job.waveform_peaks || [];", TEMPLATE)
        self.assertIn("captionCues = job.caption_cues || [];", TEMPLATE)
        self.assertIn("state.captionsAvailable = !!job.captions_available;",
                      TEMPLATE)
        self.assertIn("function renderWaveform()", TEMPLATE)
        self.assertIn("function renderCaptionRow()", TEMPLATE)
        timeline = between("function renderTimeline()", "function changeTimesEdited")
        self.assertIn("renderCaptionRow();", timeline)
        self.assertIn("renderWaveform();", timeline)


class RowBandFitTests(unittest.TestCase):
    """The row's dragged height and the band's height share one
    budget (fit fix, found by the 0.11.1 verifier): dragging the
    workspace row tall used to push the band out of the window,
    because the row's clamp was written before the band existed
    and the band never shrank. Item 4's rule: the band shrinks
    to its minimum before anything is pushed out of view."""

    def test_fit_function_exists_and_is_called_from_the_setters(self):
        self.assertIn("function fitRowBand()", TEMPLATE)
        self.assertIn("fitRowBand();", between(
            "function applyLayout()", "function clearExpandedPanel"))
        self.assertIn("fitRowBand();", between(
            "function setRowHeight(px)", "function wireRowDivider()"))
        self.assertIn("fitRowBand();", between(
            "function wireRowDivider()", "// -- stage timeline marks"))
        self.assertIn('window.addEventListener("resize", '
                      'function () { fitRowBand(); });', TEMPLATE)

    def test_band_yields_to_its_minimum_before_the_row_yields(self):
        fit = between("function fitRowBand()",
                      "function applyLayout()")
        band_cut = fit.index("band -= cut;")
        row_cut = fit.index("splitH = Math.max(320, budget - band);")
        self.assertLess(band_cut, row_cut)
        self.assertIn("band - 156", fit)
        # The remembered heights are never rewritten: the fit
        # writes the applied values to the style properties and
        # keeps the wanted height in its own variable.
        self.assertIn("var rowHeightWanted = null;", TEMPLATE)
        self.assertIn('split.style.setProperty("--pane-height", '
                      'splitH + "px");', fit)
        self.assertIn('bandEl.style.setProperty("--band-height", '
                      'band + "px");', fit)

    def test_band_minimum_keeps_every_row_inside_the_band(self):
        # The floor lives in the height clamp, and the waveform
        # row is the one row that can give inside the band: it
        # flexes down to a thin strip at the minimum instead of
        # spilling below the band's own box.
        self.assertIn(
            "height: clamp(156px, var(--band-height, 176px), "
            "360px);", TEMPLATE)
        self.assertIn(
            ".tl-wave-row { position: relative; flex: 1 1 auto; "
            "min-height: 10px;", TEMPLATE)

    def test_row_height_is_remembered_as_asked_not_as_fitted(self):
        setter = between("function setRowHeight(px)",
                         "function wireRowDivider()")
        self.assertIn("rowHeightWanted = clampRowHeight(px);", setter)
        self.assertIn('window.localStorage.setItem('
                      '"tye-pane-height", rowHeightWanted + "px");',
                      setter)


if __name__ == "__main__":
    unittest.main()
