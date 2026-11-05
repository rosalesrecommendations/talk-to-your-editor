"""v0.15 Batch I: timeline, transport, and the Make the edit area.

Items 30, 31, 33, 34, 35, 38, 39 from Justin's Oct 30 hand-test:

- 31: Fit is a reset, not a lock: the first drag on the band
  after Fit leaves the fitted view and pans; Fit re-applies.
- 30: one zoom state for every input (slider, +/-, wheel,
  pinch, Fit), timestamps stepping honestly on the ruler, the
  waveform drawn from the edited clock's peaks, and a short
  fixed crossfade at every join in the app's own rendered
  output when "Smooth the joins (crossfade)" is on.
- 33/35: Edit settings lives in a slim bar above the timeline
  band; the transport row carries the Remove family as quick
  actions after a first edit; both write the one stored
  setting through the clean-up path, with the Editor panel
  chips as the third surface.
- 34: Vertical/Landscape is one two-segment pill; the round
  "Talk" button (the voice-command dictation button, which
  turns red and pulses "Recording" while it listens, and so
  read as an unexplained record button) is the Batch D mic
  icon button beside a plain "Say a direction" label.
- 38: every top-bar quick action is a direct button; no
  three-dots overflow exists in the header.
- 39: transport buttons are icon + word pairs side by side
  (20px icon, 13px word, 46 tall), the mirror pair equal,
  Zoom % a plain readout, Fit at the row's 46.

Everything is offline; the one ffmpeg render runs on a tiny
generated clip, the same way the older splice tests do.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talk_to_your_editor import intake_build, usual, webapp  # noqa: E402
from talk_to_your_editor import transcribe as speech  # noqa: E402

TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")

FFMPEG = shutil.which("ffmpeg")


def between(start: str, end: str, text: str = TEMPLATE) -> str:
    i = text.index(start)
    return text[i:text.index(end, i)]


class FitResetTests(unittest.TestCase):
    """Item 31: Fit is a reset, not a lock."""

    def test_fit_pan_helpers_exist_and_write_the_one_zoom(self):
        for name in ("tlFitPanDown", "tlFitPanDrag", "tlFitPanUp"):
            self.assertIn(f"function {name}(", TEMPLATE)
        block = between("function tlFitPanDrag", "// Pinch zoom")
        # Leaving Fit goes through the same tlSetZoom as every
        # other input, at the pre-Fit zoom or the reading size.
        self.assertIn("tlSetZoom(target, tlFitPan.time, tlFitPan.offX)", block)
        self.assertIn("tlPrevPxPerSec", block)
        self.assertIn("TL_READ_PPS", block)
        # And the pan itself is the scroller's own offset.
        self.assertIn("tlScrollEl.scrollLeft", block)

    def test_all_three_strip_surfaces_offer_the_fit_pan(self):
        self.assertEqual(TEMPLATE.count("tlFitPanDown(e);"), 3)
        self.assertEqual(TEMPLATE.count("tlFitPanDrag(e))"), 3)

    def test_fit_toggle_itself_is_unchanged(self):
        block = between("function tlToggleFit", "function tlResetZoom")
        self.assertIn("tlPrevPxPerSec = tlScale()", block)
        self.assertIn("tlSetZoom(tlFitScale())", block)


class ZoomInputTests(unittest.TestCase):
    """Item 30a: one zoom state, every input writing it."""

    def test_wheel_zoom_is_pointer_centred_through_tlsetzoom(self):
        block = between('addEventListener("wheel"', "function tlZoomKeyOk")
        self.assertIn("e.ctrlKey || e.metaKey", block)
        self.assertIn("var timeAt = (tlScrollEl.scrollLeft + offX) / tlScale();", block)
        self.assertIn("tlSetZoom(tlScale() * Math.exp(-e.deltaY * 0.0025), timeAt, offX)", block)

    def test_pinch_zoom_writes_the_same_state(self):
        block = between("// Pinch zoom on the band", "// Fit toggles")
        self.assertIn("tlSetZoom(", block)
        self.assertIn("tlPinch.scale * (mid.dist / tlPinch.dist)", block)
        self.assertIn("tlPinching = true", block)
        # While a pinch runs the scrub handlers stand down.
        self.assertEqual(TEMPLATE.count("if (tlPinching) { return; }"), 6)

    def test_ruler_steps_come_from_the_one_interval_table(self):
        block = between("function tlRulerInterval", "function renderRuler")
        self.assertIn("[0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]", block)
        # Labels are drawn on the edited clock by renderRuler.
        ruler = between("function renderRuler", "// -- overview strip")
        self.assertIn("label.textContent = fmtTransport(t);", ruler)


class WaveformTests(unittest.TestCase):
    """Item 30c: the peaks are the edited clock's own shape."""

    def test_peaks_map_segments_onto_the_output_clock(self):
        import array

        class Samples(array.array):
            sample_rate = 16000

        samples = Samples("f", [0.0] * 16000 * 4)
        for i in range(16000, 32000):
            samples[i] = 0.9  # the loud second lives in segment 2
        segments = [
            {"duration": 1.0, "range": {"start": 3.0, "end": 4.0}},
            {"duration": 1.0, "range": {"start": 1.0, "end": 2.0}},
        ]
        peaks = webapp.waveform_peaks_for_samples(samples, segments, count=4)
        self.assertEqual(len(peaks), 4)
        # Output slices 0-1 are the quiet source second (3-4s);
        # slices 2-3 are the loud source second (1-2s), because
        # the peaks follow the edited order, not the source's.
        self.assertEqual(peaks[0], 0.0)
        self.assertGreater(peaks[3], 0.9)


class CrossfadeGraphTests(unittest.TestCase):
    """Item 30d: the splice graph, with and without the fade."""

    RANGES = [
        {"start": 0.0, "end": 5.0},
        {"start": 7.0, "end": 12.0},
        {"start": 20.0, "end": 25.0},
    ]

    def test_crossfade_on_fades_every_join_and_cuts_the_picture(self):
        graph = intake_build.splice_filter_graph(self.RANGES, True, 0.04)
        self.assertEqual(graph.count("acrossfade=d=0.0400"), 2)
        self.assertIn("concat=n=3:v=1:a=0[outv]", graph)
        self.assertIn("[outa]", graph)
        self.assertEqual(intake_build.CROSSFADE_S, 0.04)

    def test_crossfade_off_is_the_plain_concat(self):
        graph = intake_build.splice_filter_graph(self.RANGES, True, 0.0)
        self.assertNotIn("acrossfade", graph)
        self.assertIn("concat=n=3:v=1:a=1[outv][outa]", graph)

    def test_no_audio_never_fades(self):
        graph = intake_build.splice_filter_graph(self.RANGES, False, 0.04)
        self.assertNotIn("acrossfade", graph)
        self.assertNotIn("[outa]", graph)

    def test_a_short_piece_shortens_its_fade(self):
        ranges = [{"start": 0.0, "end": 5.0}, {"start": 7.0, "end": 7.05}]
        graph = intake_build.splice_filter_graph(ranges, True, 0.04)
        self.assertIn("acrossfade=d=0.0225", graph)

    def test_one_range_has_no_join_to_fade(self):
        graph = intake_build.splice_filter_graph(self.RANGES[:1], True, 0.04)
        self.assertNotIn("acrossfade", graph)


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class CrossfadeRenderTests(unittest.TestCase):
    """A real render with the fade on completes and plays."""

    def test_render_with_crossfade_completes(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-i-cf-"))
        try:
            source = tmp / "source.mp4"
            subprocess.run(
                [
                    FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
                    "-f", "lavfi", "-i", "sine=frequency=440",
                    "-t", "6", str(source),
                ],
                check=True,
            )
            cuts = {
                "kept_ranges": [
                    {"start": 0.0, "end": 2.0},
                    {"start": 3.0, "end": 5.0},
                ]
            }
            out = tmp / "out.mp4"
            intake_build.render_splice(source, cuts, out, crossfade_s=0.04)
            info = speech.probe_media(out)
            self.assertAlmostEqual(info["duration"], 4.0, delta=0.25)
            self.assertTrue(info["has_audio"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class CrossfadeSettingTests(unittest.TestCase):
    """The switch is one stored front-door setting, default on."""

    def test_defaults_and_usual_carry_the_choice(self):
        self.assertTrue(webapp.FRONT_DOOR_DEFAULTS["crossfade"])
        self.assertIn("crossfade", usual.USUAL_KEYS)
        cleaned = usual.load_usual(dict(webapp.FRONT_DOOR_DEFAULTS))
        self.assertIn("crossfade", cleaned)

    def test_the_switch_is_in_edit_settings_with_its_hint(self):
        self.assertIn('id="fdCrossfade"', TEMPLATE)
        block = between('data-tool="crossfade"', 'id="passList"')
        self.assertIn("Smooth the joins (crossfade)", block)
        self.assertIn("The picture still cuts", block)
        # The front-door post carries the switch's state.
        self.assertIn(
            "crossfade: fdCrossfade ? !!fdCrossfade.checked : true", TEMPLATE
        )


class SettingsBarTests(unittest.TestCase):
    """Item 35: Edit settings above the band; the card keeps
    Make the edit and the format pill."""

    def test_bar_sits_directly_above_the_band(self):
        self.assertLess(
            TEMPLATE.index('id="editSettingsBar"'),
            TEMPLATE.index('id="timelineBand"'),
        )

    def test_edit_settings_lives_in_the_bar_not_the_card(self):
        bar = between('id="editSettingsBar"', 'id="timelineBand"')
        self.assertIn('id="editSettings"', bar)
        self.assertIn('id="fdRetakes"', bar)
        self.assertIn('id="fdModePills"', bar)
        card = between('id="makeEditCard"', 'id="editSettingsBar"')
        self.assertNotIn('id="editSettings"', card)
        self.assertNotIn('id="fdRetakes"', card)

    def test_the_card_keeps_the_format_pill(self):
        card = between('id="makeEditCard"', 'id="editSettingsBar"')
        self.assertIn('id="formatRow"', card)
        self.assertIn('id="btnV"', card)
        self.assertIn('id="btnL"', card)

    def test_bar_tool_rows_are_wired(self):
        self.assertIn(
            'wireToolRows(document.getElementById("editSettingsBar"));',
            TEMPLATE,
        )

    def test_fit_budget_and_band_growth_know_the_new_surfaces(self):
        # fitRowBand's budget subtracts the bar and plans for
        # the band's growth; the growth for the quick actions'
        # own line is measured by syncBandGrowth (the v0.12
        # wrap mechanism), not guessed from the width.
        self.assertIn("barEl.offsetHeight + 8", TEMPLATE)
        self.assertIn(
            "body.job-loaded.quick-line .timeline-band { height: "
            "calc(clamp(180px, var(--band-height, 200px), 360px) + 54px); }",
            TEMPLATE,
        )
        self.assertIn("function syncBandGrowth()", TEMPLATE)
        self.assertIn("function tlBandGrowthPx()", TEMPLATE)
        self.assertIn(
            'document.body.classList.toggle("has-edit", !!view.has_edit);',
            TEMPLATE,
        )


class TransportQuickActionTests(unittest.TestCase):
    """Items 33: the Remove family in the transport row."""

    def test_the_row_carries_the_three_quick_actions(self):
        self.assertIn('id="tlQuickActions" hidden', TEMPLATE)
        block = between("var TL_QUICK_CHIPS", "function renderTlQuick")
        self.assertIn('["remove_retakes", "Remove retakes"]', block)
        self.assertIn('["remove_fillers", "Remove filler words"]', block)
        self.assertIn('["cut_silence", "Word gaps"]', block)
        # No studio-sound action was invented.
        self.assertNotIn("studio", TEMPLATE.lower())

    def test_shown_only_after_a_first_edit(self):
        block = between("function renderThreadView", "editorNameStr")
        self.assertIn('tlQuick.hidden = !view.has_edit;', block)

    def test_quick_actions_flip_through_the_cleanup_path(self):
        block = between("function cleanupChipEl", "// The transport row's")
        self.assertIn("flipCleanup(spec[0])", block)


class MakeEditAreaTests(unittest.TestCase):
    """Item 34: the format pill and the Talk button's fate."""

    def test_format_is_one_two_segment_pill(self):
        row = between('id="formatRow"', 'id="fmtNote"')
        self.assertIn('class="mode-pill"', row)
        self.assertIn('class="mode-seg on" type="button" aria-pressed="true">Vertical 9:16<', row)
        self.assertIn('class="mode-seg" type="button" aria-pressed="false">Landscape 16:9<', row)
        self.assertNotIn("Vertical (9:16)", TEMPLATE)
        self.assertNotIn("Landscape (16:9)", TEMPLATE)

    def test_setfmt_flips_the_pills_pressed_state(self):
        block = between("function setFmt", "function postJson")
        self.assertIn('setAttribute("aria-pressed"', block)

    def test_talk_is_the_mic_button_beside_say_a_direction(self):
        row = between("Say a direction", 'id="voiceText"')
        self.assertIn('id="voiceTalkBtn" class="dictate-btn"', row)
        self.assertIn('title="Dictate" aria-label="Dictate"', row)
        # The word Talk is not the button's label any more.
        self.assertNotIn('id="voiceTalkBtn" type="button">Talk<', TEMPLATE)
        # Its wiring is untouched: it still dictates voice
        # commands through the one dictation route.
        self.assertIn('wireDictation("voiceTalkBtn", "voiceText", "voiceStatus");', TEMPLATE)


class TopBarTests(unittest.TestCase):
    """Item 38: all quick actions direct, no overflow-only home."""

    def test_all_seven_actions_are_direct_buttons(self):
        head = between('<header class="app-head">', "</header>")
        for btn in (
            "bringInBtn", "teachToggle", "showVideoBtn",
            "showTranscriptBtn", "howItWorksToggle",
            "commentsToggle", "settingsToggle",
        ):
            self.assertIn(f'id="{btn}"', head)

    def test_no_three_dots_overflow_in_the_header(self):
        head = between('<header class="app-head">', "</header>")
        self.assertNotIn("&#8943;", head)
        self.assertNotIn("⋯", head)
        self.assertNotIn("overflow", head)


class TransportRowTests(unittest.TestCase):
    """Item 39: icon + word pairs, 46 tall, equal mirror pair."""

    def test_button_geometry_is_the_row_treatment(self):
        css = between(".tp-btn {", ".tp-play {")
        self.assertIn("flex-direction: row", css)
        self.assertIn("height: 46px", css)
        self.assertIn("gap: 8px", css)

    def test_icon_and_word_sizes(self):
        self.assertIn(".tp-icon { font-size: 20px;", TEMPLATE)
        self.assertIn(".tp-label { font-size: 13px;", TEMPLATE)

    def test_mirror_pair_shares_one_width(self):
        m = re.search(r"#back5, #fwd5 \{ width: (\d+)px;", TEMPLATE)
        self.assertIsNotNone(m)

    def test_zoom_readout_is_a_plain_span(self):
        self.assertIn(
            '<span class="tl-zoom-readout" id="tlZoomReadout">Zoom 100%</span>',
            TEMPLATE,
        )
        self.assertIn("#tlFitBtn { height: 46px;", TEMPLATE)

    def test_play_disc_keeps_its_locked_exception(self):
        css = between(".tp-play {", "/* Timeline panel (v2)")
        self.assertIn("width: 52px", css)
        self.assertIn("height: 52px", css)
        self.assertIn("border-radius: 999px", css)


if __name__ == "__main__":
    unittest.main()
