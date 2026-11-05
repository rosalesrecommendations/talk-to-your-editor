"""v0.12, change 1: the timeline band zooms and scrolls.

From Justin's Oct 29, 2026 review of 0.11.1 (PLAN.md, timeline
item): the band's strip becomes a zoomable, scrollable word
strip. The content lives in content coordinates (content width
= edit duration x px per second) inside a sideways-scrolling
viewport; zoom runs from Fit (the whole edit in view) up to at
least 200 px/s, with the reading size settled at 120 px/s and
the readout a plain percent (100% = 120 px/s). Words print
inside the segment blocks only when the whole word fits its
measured text width; words that do not fit merge into a group
block, and nothing is ever truncated or ellipsised. A thin
overview strip above the ruler always shows the whole edit
with a box for the visible window. Zoom and scroll are
remembered in the layout store; playback page-scrolls to keep
the playhead in view.

Pinned here, at source level, in the style of the 0.11.1 band
tests (the scripted probes live in the fit harness):

- T1: zoom settable from Fit to at least 200 px/s by slider,
  buttons, and Cmd/Ctrl+= / -; the readout shows the percent;
- T2: the word-fit rule (canvas measureText, full text or the
  group block, never an ellipsis);
- T3: the scroll container, plain wheel scrolling sideways,
  Cmd/Ctrl+wheel zooming around the pointer's time;
- T4: the Fit toggle, by its button and by Shift+Z;
- T5: page-scroll follow during playback;
- T6: every segment keeps a 1px floor at any zoom;
- T7: the ruler's major interval adapts to the scale;
- T8: zoom and scroll ride in the layout store and Reset
  layout puts the zoom back to Fit;
- the overview strip, its viewport box, and its click/drag;
- the waveform drawing the visible window only, on the same
  peaks data path with the same DPR handling;
- the Fit overflow rule: boxes centred on the strip (the
  dot's hover tip, the ruler's labels) and the caption blocks
  are clamped inside the content at every render, so a
  resting box can never leave the scroller a scroll range
  at Fit after zooming.

Everything is offline; no provider is called.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start_marker: str, end_marker: str) -> str:
    start = TEMPLATE.index(start_marker)
    end = TEMPLATE.index(end_marker, start)
    return TEMPLATE[start:end]


def ruler_interval(scale: float) -> float:
    """A mirror of tlRulerInterval's arithmetic: the first
    listed interval that lands at least 80px apart on screen."""
    for step in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300):
        if step * scale >= 80:
            return step
    return 300


def centred_left(center_px: float, box_width_px: float,
                 content_w: float) -> float:
    """A mirror of tlCentredLeft's arithmetic: the centre a
    box centred on a spot on the strip may use, shifted
    sideways by half the box's width (plus 2px) when the spot
    sits near either end of the content."""
    if not content_w or not box_width_px > 0:
        return center_px
    half = box_width_px / 2 + 2
    if content_w <= box_width_px + 4:
        return content_w / 2
    return max(half, min(content_w - half, center_px))


class ZoomControlsTests(unittest.TestCase):
    """T1: the zoom cluster, its range, and the plain readout."""

    def test_zoom_cluster_lives_in_the_band_transport_row(self):
        row = between('id="fmtRow"', 'id="tlbar"')
        for marker in (
            'id="tlZoomOut"',
            'id="tlZoomSlider"',
            'id="tlZoomIn"',
            'id="tlZoomReadout"',
            'id="tlFitBtn"',
        ):
            self.assertIn(marker, row)

    def test_zoom_controls_are_real_focusable_elements(self):
        # Buttons are buttons and the slider is a range input,
        # so every zoom control is keyboard-operable as it is.
        self.assertIn('<button id="tlZoomOut" type="button"', TEMPLATE)
        self.assertIn('<button id="tlZoomIn" type="button"', TEMPLATE)
        self.assertIn('<button id="tlFitBtn" type="button"', TEMPLATE)
        self.assertIn('<input type="range" id="tlZoomSlider"', TEMPLATE)

    def test_zoom_range_reaches_at_least_200_px_per_second(self):
        self.assertIn("var TL_ZOOM_MAX = 200;", TEMPLATE)
        self.assertIn(
            "Math.max(fit, Math.min(TL_ZOOM_MAX, newScale))", TEMPLATE
        )
        self.assertIn("function tlSetZoom(newScale, anchorTime, anchorOffsetX)",
                      TEMPLATE)
        self.assertIn("function tlFitScale()", TEMPLATE)

    def test_readout_is_the_plain_percent_of_reading_size(self):
        self.assertIn("var TL_READ_PPS = 120;", TEMPLATE)
        chrome = between("function tlSyncZoomChrome()", "function tlAfterScroll()")
        self.assertIn('"Zoom " +', chrome)
        self.assertIn("Math.round(scale / TL_READ_PPS * 100)", chrome)
        self.assertIn('id="tlZoomReadout">Zoom 100%<', TEMPLATE)

    def test_buttons_slider_and_keys_all_drive_the_zoom(self):
        self.assertIn('tlZoomInBtn.addEventListener("click"', TEMPLATE)
        self.assertIn('tlZoomOutBtn.addEventListener("click"', TEMPLATE)
        self.assertIn("tlZoomBy(1.3);", TEMPLATE)
        self.assertIn("tlZoomBy(1 / 1.3);", TEMPLATE)
        self.assertIn('tlZoomSlider.addEventListener("input"', TEMPLATE)
        keys = between("function tlZoomKeyOk(e)", "window.addEventListener(\"resize\"")
        self.assertIn('e.key === "=" || e.key === "+"', keys)
        self.assertIn('e.key === "-" || e.key === "_"', keys)
        self.assertIn("e.metaKey || e.ctrlKey", keys)

    def test_key_zoom_anchors_on_the_playhead_when_it_is_visible(self):
        anchor = between("function tlZoomAnchor()", "function tlZoomBy(factor)")
        self.assertIn("displayTime()", anchor)
        self.assertIn("pt * scale >= sl && pt * scale <= sl + vw", anchor)
        # Otherwise it anchors on the middle of the window.
        self.assertIn("(sl + vw / 2) / scale", anchor)


class WordFitTests(unittest.TestCase):
    """T2: full text only when the word fits; never truncated."""

    def test_words_are_measured_with_canvas_measure_text(self):
        self.assertIn("function tlTextWidth(txt)", TEMPLATE)
        self.assertIn("tlMeasureCtx.measureText(txt).width", TEMPLATE)
        self.assertIn('tlMeasureCtx.font = "11px system-ui, sans-serif";',
                      TEMPLATE)

    def test_the_fit_rule_and_the_group_block(self):
        blocks = between("function renderBlocks()", "function renderTlCaption()")
        self.assertIn("if (wp >= tlTextWidth(text) + 4)", blocks)
        self.assertIn('"tl-word" + (w.status === "cut" ? " cut" : "")', blocks)
        self.assertIn("wd.textContent = text;", blocks)
        self.assertIn('"tl-word-group" + (group.allCut ? " cut" : "")', blocks)
        self.assertIn("flushGroup();", blocks)

    def test_no_word_is_ever_truncated_or_ellipsised(self):
        word_css = between(".tl-word {", "}")
        self.assertNotIn("text-overflow", word_css)
        self.assertIn("white-space: nowrap", word_css)
        blocks = between("function renderBlocks()", "function renderTlCaption()")
        self.assertNotIn("text-overflow", blocks)
        self.assertNotIn("slice(0", blocks)
        self.assertNotIn("substring", blocks)

    def test_cut_words_keep_the_dimmed_struck_look(self):
        # v0.15 Batch H (item 25) supersedes the band's cut
        # look: the red text color disagreed with the
        # transcript's cut words (P8). A band cut word now
        # takes the transcript's scheme: the kept color, the
        # strike, and the same 0.62 low rung, groups included.
        self.assertIn(
            ".tl-word.cut { color: #eef4ff; text-decoration: line-through;",
            TEMPLATE,
        )
        self.assertIn(".tl-word-group.cut { opacity: 0.62;", TEMPLATE)


class ScrollAndWheelTests(unittest.TestCase):
    """T3: content coordinates in a scrollable viewport."""

    def test_the_strip_lives_in_a_sideways_scroller(self):
        self.assertIn('id="tlScroll"', TEMPLATE)
        scroll_css = between(".tl-scroll {", "}")
        self.assertIn("overflow-x: auto", scroll_css)
        self.assertIn("overflow-y: hidden", scroll_css)
        band = between('id="timelineBand"', "</main>")
        self.assertLess(band.index('id="tlScroll"'), band.index('id="tlStack"'))
        self.assertLess(band.index('id="tlStack"'), band.index('id="tlCaption"'))

    def test_content_width_is_duration_times_px_per_second(self):
        self.assertIn("function tlContentWidthPx()", TEMPLATE)
        content = between("function tlContentWidthPx()", "function tlSyncContentWidth()")
        self.assertIn("Math.round(d * scale)", content)
        self.assertIn('stack.style.width = w + "px";', TEMPLATE)

    def test_plain_wheel_scrolls_and_ctrl_wheel_zooms_on_the_pointer(self):
        wheel = between('addEventListener("wheel"', "function tlZoomKeyOk(e)")
        self.assertIn("e.ctrlKey || e.metaKey", wheel)
        self.assertIn("e.preventDefault();", wheel)
        # The time under the pointer is computed before the zoom
        # and handed to it as the anchor, so that time stays put.
        self.assertIn(
            "var timeAt = (tlScrollEl.scrollLeft + offX) / tlScale();", wheel
        )
        self.assertIn("tlSetZoom(tlScale() * Math.exp(-e.deltaY * 0.0025), timeAt, offX);",
                      wheel)
        self.assertIn("tlScrollEl.scrollLeft += dx;", wheel)

    def test_the_band_still_seeks_from_every_row(self):
        # The 0.11.1 seek paths ride the stack's own rectangle,
        # which in content coordinates already accounts for the
        # scroll, so band, ruler, wave, and caption clicks seek
        # exactly as they did.
        seek = between("function tlSeekEvent(e)", "function tlHoverEvent")
        self.assertIn('document.getElementById("tlStack")', seek)
        self.assertIn("editedToFileTime(et)", seek)


class FitToggleTests(unittest.TestCase):
    """T4: Fit toggles back to the previous zoom."""

    def test_fit_toggle_remembers_the_previous_zoom(self):
        toggle = between("function tlToggleFit()", "function tlResetZoom()")
        self.assertIn("tlPrevPxPerSec = tlScale();", toggle)
        self.assertIn("tlSetZoom(tlFitScale());", toggle)
        self.assertIn("tlSetZoom(tlPrevPxPerSec, displayTime());", toggle)
        self.assertIn('tlFitBtn.addEventListener("click"', TEMPLATE)

    def test_shift_z_runs_the_same_toggle(self):
        keys = between("function tlZoomKeyOk(e)", "window.addEventListener(\"resize\"")
        self.assertIn('e.key === "Z" || e.key === "z"', keys)
        self.assertIn("tlToggleFit();", keys)


class FitOverflowTests(unittest.TestCase):
    """Fit must fit: nothing centred on the strip may hang past
    the content's edge, because a hanging box counts as
    scrollable overflow for the strip's scroller even at rest
    (the dot's hover tip sits at opacity 0 until hovered). On
    the measured 605s edit at 1440x900, after zooming to
    120 px/s and clicking the overview, Fit set the stack's
    width to the window's 1398px but its scroll width stayed
    1490: a 200px tip centred on a dot near the end stuck
    92px past the edge, with a ruler label (~3px) and a
    caption block (~6px) riding along. Every re-render now
    clamps those boxes inside the content, so the scroll
    range at Fit is the ticks' pixel at most."""

    def test_the_centre_clamp_shifts_by_half_the_box(self):
        self.assertIn("function tlCentredLeft(centerPx, boxWidthPx)",
                      TEMPLATE)
        body = between("function tlCentredLeft(centerPx, boxWidthPx)",
                       "function seekTo(ts, fmt)")
        self.assertIn("tlContentWidthPx()", body)
        self.assertIn("boxWidthPx / 2 + 2", body)
        self.assertIn(
            "Math.max(half, Math.min(w - half, centerPx))", body)

    def test_the_tip_is_shifted_inside_the_content(self):
        dots = between("function renderDots()", "function playbackBuilding()")
        # The dot itself still sits where tlInsetLeft puts it,
        # and the tip's resting look is unchanged: centred on
        # the dot, shown on hover or focus.
        self.assertIn("dot.style.left = tlInsetLeft(g.ts / d, 8);", dots)
        tip_css = between(".tl-tip {", "}")
        self.assertIn("left: 50%", tip_css)
        self.assertIn("transform: translateX(-50%)", tip_css)
        self.assertIn(
            ".tl-dot:hover .tl-tip, .tl-dot:focus .tl-tip { opacity: 1; }",
            TEMPLATE)
        # Once the dot is on the page, the tip is measured and
        # its centre shifted to the clamped centre.
        self.assertIn("var tipW = tip.offsetWidth;", dots)
        self.assertIn("var dotPx = parseFloat(dot.style.left);", dots)
        self.assertIn("tlCentredLeft(dotPx, tipW) - dotPx", dots)
        self.assertIn('tip.style.left = "calc(50% + "', dots)

    def test_the_measured_tip_no_longer_reaches_past_fit(self):
        # The measured case: content 1398px (the window at Fit),
        # a 200px tip centred on a dot inset 8px from the end.
        centre = centred_left(1398 - 8, 200, 1398)
        self.assertEqual(centre, 1296)
        self.assertLessEqual(centre + 200 / 2, 1398)
        # A dot at the start shifts the other way, a dot in the
        # middle is not moved at all, and the same clamp holds
        # zoomed in (605s at 120 px/s), where the tip then sits
        # wholly inside the window at the strip's far scroll.
        self.assertEqual(centred_left(8, 200, 1398) - 200 / 2, 2)
        self.assertEqual(centred_left(699, 200, 1398), 699)
        wide = 605 * 120
        self.assertLessEqual(
            centred_left(wide - 8, 200, wide) + 200 / 2, wide)

    def test_ruler_labels_are_clamped_the_same_way(self):
        ruler = between("function renderRuler()", "// -- overview strip")
        self.assertIn(
            "tlCentredLeft(t * scale, label.offsetWidth)", ruler)
        # A label for a mark near the end (a ~34px "10:05" a
        # few pixels short of it) lands wholly inside too.
        self.assertLessEqual(
            centred_left(1398 - 14, 34, 1398) + 34 / 2, 1398)

    def test_caption_blocks_stop_at_the_content_edge(self):
        row = between("function renderCaptionRow()", "function renderWaveform()")
        self.assertIn("var capContentW = tlContentWidthPx();", row)
        self.assertIn("capContentW - cue.start * capScale", row)
        # A cue ending at the edit's end: its block's right
        # edge is the narrower of its own width and the room
        # left in the content, so it cannot reach past it.
        start, end, scale, content_w = 590.0, 605.0, 2.32, 1398
        width = max(1, min((end - start) * scale,
                           content_w - start * scale))
        self.assertLessEqual(start * scale + width, content_w)


class FollowTests(unittest.TestCase):
    """T5: page-scroll follow keeps the playhead in the window."""

    def test_follow_pages_when_the_playhead_reaches_the_right_edge(self):
        follow = between("function tlFollowPlayhead()", "// -- the timeline panel")
        self.assertIn("vid.paused", follow)
        self.assertIn("tlIsFit()", follow)
        self.assertIn("if (px < sl || px > sl + vw)", follow)
        self.assertIn("tlScrollEl.scrollLeft = Math.max(0, px - 24);", follow)

    def test_follow_runs_from_the_playhead_update(self):
        playhead = between("function updatePlayhead()", "function tlFollowPlayhead()")
        self.assertIn("tlPlayhead.style.left = tlInsetLeft(frac, 6);", playhead)
        self.assertIn("tlFollowPlayhead();", playhead)


class SegmentFloorTests(unittest.TestCase):
    """T6: every segment renders at least 1px wide at any zoom."""

    def test_segment_blocks_have_a_one_pixel_floor(self):
        blocks = between("function renderBlocks()", "function renderTlCaption()")
        self.assertIn(
            'block.style.width = Math.max(1,', blocks)
        self.assertIn(
            "(piece.output_end - piece.output_start) * scale", blocks)
        # Word group blocks keep the same floor.
        self.assertIn('g.style.width = Math.max(1,', blocks)

    def test_positions_are_content_pixels_not_percents(self):
        blocks = between("function renderBlocks()", "function renderTlCaption()")
        self.assertIn('block.style.left = (piece.output_start * scale) + "px";',
                      blocks)
        ruler = between("function renderRuler()", "// -- overview strip")
        self.assertIn('secondTick.style.left = (s * scale) + "px";', ruler)


class RulerIntervalTests(unittest.TestCase):
    """T7: the major-label interval adapts to the scale."""

    def test_the_interval_function_steps_by_screen_distance(self):
        self.assertIn("function tlRulerInterval(scale)", TEMPLATE)
        body = between("function tlRulerInterval(scale)", "function renderRuler()")
        self.assertIn("[0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]", body)
        self.assertIn("steps[i] * scale >= 80", body)
        self.assertIn("ruler.dataset.majorInterval = step;", TEMPLATE)

    def test_interval_bounds_at_fit_and_at_reading_size(self):
        # Fit for a long edit: a 678s edit in a 1200px window is
        # about 1.77 px/s, and the interval must be at least 30s.
        fit_scale = 1200 / 678.65
        self.assertGreaterEqual(ruler_interval(fit_scale), 30)
        # At reading size the interval must be at most 5s.
        self.assertLessEqual(ruler_interval(120), 5)


class ZoomMemoryTests(unittest.TestCase):
    """T8: zoom and scroll survive a reload, in the layout store."""

    def test_store_keys_ride_in_the_layout_state(self):
        self.assertIn("bandZoom: null, bandScroll: 0", TEMPLATE)
        self.assertIn("parsedLayout.bandZoom", TEMPLATE)
        self.assertIn("parsedLayout.bandScroll", TEMPLATE)
        self.assertIn('"tye-layout"', TEMPLATE)

    def test_zoom_and_scroll_write_the_store(self):
        zoom = between("function tlSetZoom(newScale", "function tlZoomAnchor()")
        self.assertIn("layoutState.bandZoom = tlPxPerSec > 0 ? tlPxPerSec : null;",
                      zoom)
        self.assertIn("persistLayout();", zoom)
        after = between("function tlAfterScroll()", "function tlSetZoom(newScale")
        self.assertIn("layoutState.bandScroll = Math.round(tlScrollEl.scrollLeft);",
                      after)

    def test_the_remembered_scroll_lands_on_the_first_render(self):
        timeline = between("function renderTimeline()", "function changeTimesEdited")
        self.assertIn("tlScrollEl.scrollLeft = tlPendingScroll;", timeline)
        self.assertIn("tlPxPerSec = (typeof layoutState.bandZoom", TEMPLATE)

    def test_reset_layout_puts_the_zoom_back_to_fit(self):
        reset = between(
            'resetLayoutBtn.addEventListener("click"',
            "// -- panel arrangement",
        )
        self.assertIn("bandZoom: null", reset)
        self.assertIn("tlResetZoom();", reset)
        body = between("function tlResetZoom()", "if (tlScrollEl)")
        self.assertIn("tlPxPerSec = 0;", body)


class OverviewStripTests(unittest.TestCase):
    """The overview: whole edit, always drawn, box for the window."""

    def test_overview_markup_sits_in_the_band_above_the_ruler(self):
        band = between('id="timelineBand"', "</main>")
        for marker in ('id="tlOverview"', 'id="tlOverviewBlocks"',
                       'id="tlOverviewDots"', 'id="tlViewBox"'):
            self.assertIn(marker, band)
        order = [
            band.index('id="fmtRow"'),
            band.index('id="tlOverview"'),
            band.index('id="tlRuler"'),
        ]
        self.assertEqual(order, sorted(order))
        css = between(".tl-overview {", "}")
        self.assertIn("height: 24px", css)

    def test_overview_draws_segments_and_comment_dots_without_words(self):
        over = between("function renderOverview()", "function tlSyncOverviewBox()")
        self.assertIn("tl-overview-block", over)
        self.assertIn("tl-overview-dot", over)
        self.assertIn("commentEditedTs(c)", over)
        self.assertNotIn("tl-word", over)

    def test_the_viewport_box_is_always_rendered_and_tracks_the_window(self):
        # Even at Fit the box is drawn (full width there); it is
        # never hidden.
        self.assertNotIn('id="tlViewBox" hidden', TEMPLATE)
        box = between("function tlSyncOverviewBox()", "if (tlOverviewEl")
        self.assertIn('tlViewBoxEl.style.width = "100%";', box)
        self.assertIn("tlScrollEl.scrollLeft / contentW * 100", box)
        self.assertIn("Math.min(100, vw / contentW * 100)", box)

    def test_clicking_centres_the_window_and_dragging_the_box_scrolls(self):
        nav = between('tlOverviewEl.addEventListener("pointerdown"',
                      'tlOverviewEl.addEventListener("pointermove"')
        self.assertIn('mode: "box"', nav)
        self.assertIn("t * scale - tlScrollEl.clientWidth / 2", nav)
        move = between('tlOverviewEl.addEventListener("pointermove"',
                       "var tlOvUp")
        self.assertIn('tlOvDrag.mode === "box"', move)
        self.assertIn("tlOvDrag.startScroll +", move)


class WaveformWindowTests(unittest.TestCase):
    """The waveform draws the visible window only."""

    def test_waveform_draws_the_visible_window_from_the_same_peaks(self):
        wave = between("function renderWaveform()", "function renderTimeline()")
        # Same peaks data path, same DPR handling as 0.11.1.
        self.assertIn("waveformPeaks[", wave)
        self.assertIn("window.devicePixelRatio || 1", wave)
        # The peak a column shows is picked by its place in the
        # whole content, offset by the scroll position.
        self.assertIn("var scrollPx = tlScrollEl ? tlScrollEl.scrollLeft : 0;",
                      wave)
        self.assertIn("(scrollPx + x / dpr) / contentW", wave)
        self.assertIn('tlWaveCanvas.style.left = scrollPx + "px";', wave)


class NarrowWindowTransportTests(unittest.TestCase):
    """Under 720px the band's transport row wraps instead of
    running wider than the window. Measured in the live page
    at 640x560: the clock (about 68px), the transport centre
    (about 252px), and the zoom cluster (316px wide and
    rigid) plus the row's gaps made the page 687px wide, and
    the small-window fallback may only scroll up and down,
    never sideways. The wrap lives in the same under-720px
    block as the rest of that fallback, so the fitted sizes
    at 720px and up render exactly as before."""

    def small_block(self):
        return between("@media (max-width: 719.98px) {",
                       "/* Left column (v0.12, change 3)")

    def test_the_transport_row_and_its_centre_wrap(self):
        small = self.small_block()
        self.assertIn(
            "body.job-loaded .timeline-band .transport "
            "{ flex-wrap: wrap; }", small)
        self.assertIn(
            "body.job-loaded .timeline-band .transport-center "
            "{ flex-wrap: wrap; justify-content: center; }", small)

    def test_the_zoom_cluster_takes_its_own_line_and_may_wrap(self):
        small = self.small_block()
        # On its own wrapped line the cluster (316px) fits a
        # 640px window's band whole, slider included; capped
        # at the row's width and allowed to wrap inside its
        # line, it cannot stick out on a narrower window
        # either. Nothing is hidden and nothing shrinks: the
        # cluster's own sizes stay as they are everywhere.
        self.assertIn(
            "body.job-loaded .timeline-band .tl-zoom "
            "{ flex-wrap: wrap; max-width: 100%; }", small)
        self.assertNotIn("display: none", small)
        # v0.14 Batch B: the zoom pair and Fit are the one
        # icon-button size, 38px with radius 10 (was 32px round),
        # sized by their own ids so the band Comment button in
        # the same cluster keeps its transport-row height.
        # v0.15 Batch I, item 39: Fit left the icon tier — it
        # is a 46-tall word button in the transport row now;
        # the zoom pair keeps the 38px tier.
        self.assertIn(
            "#tlZoomOut, #tlZoomIn { width: 38px; "
            "height: 38px; min-width: 38px; min-height: 38px;",
            TEMPLATE)
        self.assertIn("#tlFitBtn { height: 46px;", TEMPLATE)
        self.assertIn('.tl-zoom input[type="range"] { width: 120px; }',
                      TEMPLATE)
        # v0.14 Batch C (SYMMETRY 11): the cluster is laid
        # out by the transport's flex row — no margin of its
        # own places it (the 0.13 free space surfaced as a
        # fractional 165.625px auto margin) — and its internal
        # gap is the scale's 8px.
        self.assertIn(
            ".tl-zoom { display: flex; align-items: center; "
            "gap: 8px; flex: none; padding-top: 8px; }",
            TEMPLATE)
        self.assertNotIn("gap: 6px; margin-left: auto", TEMPLATE)

    def test_the_band_grows_by_the_wrapped_line(self):
        # The wrapped line is 54px tall (the cluster's 46px
        # plus the row's 8px gap; v0.14 Batch B grew the
        # cluster to 44px when the zoom buttons took the one
        # 38px icon size, and Batch C moved its top padding
        # from 6px to the scale's 8px and the transport row's
        # gap from 10px to 8px, so the total is the same
        # 54px); in the stacked
        # fallback the band is in normal page flow, so it
        # grows by that much and the rows under the transport
        # keep the room they had. The fitted clamp itself is
        # untouched: the growth rides on top of it, and only
        # under 720px.
        small = self.small_block()
        self.assertIn(
            "body.job-loaded .timeline-band { height: "
            "calc(clamp(180px, var(--band-height, 200px), 360px) "
            "+ 54px); }", small)

    def test_the_wrap_stays_in_the_small_window_block(self):
        # The base transport rule gains no wrap, so at 720px
        # and up the row is the one line it has always been
        # (the fitted frame's own wrap rule there is a
        # different, older one, on the pane's row).
        base = between(".transport {", "}")
        self.assertNotIn("flex-wrap", base)
        self.assertEqual(
            TEMPLATE.count(".timeline-band .transport "
                           "{ flex-wrap: wrap; }"), 1)


class BandMinimumTests(unittest.TestCase):
    """The band minimum grew by the overview row's height."""

    def test_band_minimum_and_default_include_the_overview(self):
        self.assertIn("clamp(180px, var(--band-height, 200px), 360px)",
                      TEMPLATE)
        self.assertIn("Math.min(360, Math.max(180, Math.round(px)))",
                      TEMPLATE)
        fit = between("function fitRowBand()", "function applyLayout()")
        self.assertIn("band - 180", fit)


if __name__ == "__main__":
    unittest.main()
