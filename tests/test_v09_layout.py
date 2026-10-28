"""v0.9.0 Cluster 2: front door, layout, and progressive disclosure.

Items 9, 10, 11, 12, 13, 14, 16, 18, 19, 24, 27 from Justin's
2026-10-27 feedback. The page is one template; these tests read
that template (the same way test_v07 reads it) and pin the layout
contracts Justin asked for, plus one server-level check that the
standing choices behind the settings icon really do persist
through the usual store.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import usual, webapp  # noqa: E402

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start_marker: str, end_marker: str) -> str:
    start = TEMPLATE.index(start_marker)
    end = TEMPLATE.index(end_marker, start)
    return TEMPLATE[start:end]


class HeroTests(unittest.TestCase):
    """Item 13: Describe your edit leads the first-edit screen."""

    def test_hero_comes_before_the_remove_tools(self):
        self.assertLess(
            TEMPLATE.index('id="directionHero"'),
            TEMPLATE.index('id="inRemoveGroup"'),
        )

    def test_hero_carries_the_direction_box_and_the_primary_action(self):
        hero = between('id="directionHero"', 'id="stagedFlow"')
        self.assertIn("Describe your edit", hero)
        self.assertIn('id="intakeDirection"', hero)
        self.assertIn('id="firstEditBtn"', hero)
        self.assertIn("hero-action", hero)

    def test_remove_tools_sit_in_a_side_panel_on_desktop(self):
        self.assertIn('grid-template-areas: "hero tools" "stages tools"',
                      TEMPLATE)
        self.assertIn('#inRemoveGroup { grid-area: tools;', TEMPLATE)


class ToolRowTests(unittest.TestCase):
    """Items 11 and 12: compact rows, options on open, off collapses."""

    def test_every_tool_row_starts_with_its_options_hidden(self):
        # Five intake rows (retakes, silence, fillers, mode, and
        # the v0.10 editing style) and six review rows (those five
        # plus formats/direction).
        self.assertEqual(TEMPLATE.count('class="tool-options" hidden'), 11)
        self.assertEqual(TEMPLATE.count('class="tool-open"'), 11)

    def test_rows_open_pre_set_to_the_recommended_choice(self):
        self.assertIn("Last take stays (recommended)", TEMPLATE)
        self.assertIn("Leave 0.1 seconds (recommended)", TEMPLATE)
        self.assertIn("Cleanest cut (recommended)", TEMPLATE)
        # The recommended keep mode and involvement mode are the
        # ones the page selects on load.
        self.assertIn('setInKeepMode("last")', TEMPLATE)
        self.assertIn('setKeepMode("last")', TEMPLATE)
        self.assertIn('setInMode("cleanest")', TEMPLATE)
        self.assertIn('setFdMode("cleanest")', TEMPLATE)

    def test_turning_a_tool_off_collapses_its_options_completely(self):
        self.assertIn("function syncToolRow(row)", TEMPLATE)
        # Off means hidden, not dimmed: both buffer rows hide too.
        self.assertIn("fdBufferRow.hidden = !fdSilence.checked", TEMPLATE)
        self.assertIn("inBufferRow.hidden = !inSilence.checked", TEMPLATE)
        self.assertNotIn('style.opacity = fdSilence.checked', TEMPLATE)
        self.assertNotIn('style.opacity = inSilence.checked', TEMPLATE)

    def test_options_open_only_from_the_row_button(self):
        self.assertIn('openBtn.addEventListener("click"', TEMPLATE)
        self.assertIn('options.hidden = !show', TEMPLATE)
        self.assertIn('aria-expanded', TEMPLATE)


class FrontDoorTests(unittest.TestCase):
    """Items 10 and 27: one main action, short labels, plain drop line."""

    def test_drop_area_says_what_one_video_and_a_folder_do(self):
        line = between('id="dropAcceptsLine"', 'id="dropPickFolder"')
        self.assertIn("One video: it plays here", line)
        self.assertIn("A job folder: it opens as one job", line)

    def test_long_front_door_explanations_are_tucked_behind_expansion(self):
        self.assertNotIn(
            "Drop a runner job folder anywhere on this page and the "
            "app takes it in and runs on it. No path typing, no "
            "Terminal.",
            TEMPLATE,
        )
        drop = between('id="dropCard"', 'id="dropVideoWrap"')
        self.assertIn('<details class="more-info">', drop)
        hero = between('id="directionHero"', 'id="stagedFlow"')
        self.assertIn('<details class="more-info">', hero)


class SettingsTests(unittest.TestCase):
    """Item 9: standing choices behind a settings icon, remembered."""

    def test_settings_icon_toggles_a_settings_panel(self):
        self.assertIn('id="settingsToggle"', TEMPLATE)
        self.assertIn('aria-controls="settingsPanel"', TEMPLATE)
        self.assertIn('id="settingsPanel" hidden', TEMPLATE)
        self.assertIn("settingsPanel.hidden = !open", TEMPLATE)

    def test_run_direction_lives_inside_the_settings_panel(self):
        panel = between('id="settingsPanel"', 'id="jobIncompleteBanner"')
        self.assertIn('id="directionCard"', panel)
        self.assertIn('id="runDirect"', panel)

    def test_settings_panel_says_the_choices_are_remembered(self):
        panel = between('id="settingsPanel"', 'id="jobIncompleteBanner"')
        self.assertIn("saved as your usual choices", panel)

    def test_usual_choices_round_trip_between_sessions(self):
        # The store the front door seeds from: what one session
        # saves, the next session's defaults carry. Isolated in a
        # temp home, the same way test_v07_backend isolates it.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                saved = dict(webapp.FRONT_DOOR_DEFAULTS)
                saved["mode"] = "tailor"
                saved["keep_mode"] = "first"
                usual.save_usual(saved)
                seeded = usual.load_usual(
                    dict(webapp.FRONT_DOOR_DEFAULTS)
                )
        self.assertEqual(seeded["mode"], "tailor")
        self.assertEqual(seeded["keep_mode"], "first")


class WorkspaceTests(unittest.TestCase):
    """Items 9 and 14: horizontal workspace, resizable panes."""

    def test_review_is_a_three_pane_workspace_with_a_tools_panel(self):
        split = between('id="reviewSplit"', "</main>")
        self.assertIn('id="toolsPane"', split)
        self.assertIn('id="transcriptCard"', split)
        self.assertIn('id="makeEditCard"', split)
        for card in ("pinCard", "historyCard", "revisedCard"):
            self.assertIn(f'id="{card}"', split)

    def test_workspace_fits_the_window_and_scrolls_inside_panes(self):
        self.assertIn("body.job-loaded main", TEMPLATE)
        self.assertIn("height: 100dvh", TEMPLATE)
        self.assertIn("overflow: hidden", TEMPLATE)
        self.assertIn("body.job-loaded .review-left", TEMPLATE)
        self.assertIn("overflow-y: auto", TEMPLATE)

    def test_pane_dividers_drag_and_are_remembered(self):
        self.assertIn('id="paneDivider"', TEMPLATE)
        self.assertIn('id="toolsDivider"', TEMPLATE)
        self.assertIn("function wirePaneDivider", TEMPLATE)
        self.assertIn('"--pane-a", "tye-pane-a"', TEMPLATE)
        self.assertIn('"--pane-c", "tye-pane-c"', TEMPLATE)
        self.assertIn("localStorage.setItem", TEMPLATE)

    def test_mobile_keeps_the_stacked_layout(self):
        self.assertIn(
            ".review-split, .review-left, .review-right, .review-tools "
            "{ display: contents; }",
            TEMPLATE,
        )


class StagedFlowTests(unittest.TestCase):
    """Item 16: timeline first, explicit Transcribe, single passes."""

    def test_stages_are_listed_in_order_timeline_first(self):
        stages = between('id="stagedFlow"', 'id="inRemoveGroup"')
        self.assertIn("Your video on its timeline", stages)
        self.assertIn("Write down the words", stages)
        self.assertIn("Run the Remove passes you want", stages)
        self.assertIn("Make the edit from these stages", stages)
        self.assertLess(
            stages.index("Your video on its timeline"),
            stages.index('id="transcribeBtn"'),
        )

    def test_transcribe_runs_the_same_first_edit_with_passes_off(self):
        self.assertIn('id="stageTimeline"', TEMPLATE)
        self.assertIn("function startFirstEdit(settings, directionText)",
                      TEMPLATE)
        body = between('var transcribeBtn = ', "function dropJunk")
        self.assertIn("settings.remove_retakes = false", body)
        self.assertIn("settings.cut_silence = false", body)
        self.assertIn("settings.remove_fillers = false", body)
        self.assertIn('startFirstEdit(settings, "")', body)

    def test_bundled_first_edit_uses_the_same_start(self):
        body = between("if (firstEditBtn) {", 'var transcribeBtn = ')
        self.assertIn("startFirstEdit(", body)
        self.assertIn("collectIntakeSettings()", body)

    def test_each_pass_has_its_own_button_with_a_line_of_context(self):
        passes = between('id="passList"', 'id="fdFire"')
        for btn in ("passRetakesBtn", "passSilenceBtn", "passFillersBtn"):
            self.assertIn(f'id="{btn}"', passes)
        self.assertIn("When a line is said more than once", passes)
        self.assertIn("Pauses over the buffer are shortened", passes)
        self.assertIn("Filler words like um and uh come out", passes)

    def test_a_pass_button_fires_the_shared_make_the_edit_run(self):
        self.assertIn("function wirePassButton", TEMPLATE)
        self.assertIn("fdFire.click()", TEMPLATE)


class ReviewScreenTests(unittest.TestCase):
    """Items 18 and 19: slim intake bar; Remove in exactly one place."""

    def test_intake_card_collapses_to_a_slim_bar_once_a_job_is_loaded(self):
        self.assertIn('id="dropSlimBar"', TEMPLATE)
        self.assertIn('id="dropSlimToggle"', TEMPLATE)
        self.assertIn('document.body.classList.add("job-loaded")', TEMPLATE)
        self.assertIn("body.job-loaded #dropCard .drop-full", TEMPLATE)
        self.assertIn("display: none", TEMPLATE)

    def test_review_remove_settings_are_a_compact_details_affordance(self):
        card = between('id="makeEditCard"', 'id="transcriptCard"')
        self.assertIn('<details class="settings-details" id="editSettings">',
                      card)
        self.assertIn("Edit settings", card)
        self.assertNotIn('<h3 class="remove-heading">Remove</h3>', card)
        # The primary action stays outside the collapsed settings.
        self.assertLess(card.index("</details>"), card.index('id="fdFire"'))

    def test_remove_controls_exist_once_per_surface(self):
        self.assertEqual(TEMPLATE.count('id="fdRetakes"'), 1)
        self.assertEqual(TEMPLATE.count('id="inRetakes"'), 1)
        self.assertEqual(TEMPLATE.count('id="fdDirection"'), 1)
        self.assertEqual(TEMPLATE.count('id="intakeDirection"'), 1)


class HierarchyTests(unittest.TestCase):
    """Item 24: one clearly primary action per screen, one type scale."""

    def test_primary_actions_are_visually_distinct(self):
        self.assertIn(".hero-action", TEMPLATE)
        self.assertIn("min-height: 56px", TEMPLATE)
        self.assertIn('id="firstEditBtn" class="primary hero-action"',
                      TEMPLATE)
        self.assertIn('id="fdFire" class="primary hero-action"', TEMPLATE)

    def test_type_scale_tracks_importance(self):
        self.assertIn("h1 { font-size: 32px; }", TEMPLATE)
        self.assertIn("h2 { font-size: 20px; }", TEMPLATE)
        self.assertIn(".hero-title { font-size: 24px;", TEMPLATE)


# ---------------------------------------------------------------------------
# v2 workspace (round 2 specs, 2026-10-28): the frame, the pop ups,
# the transport words, the block-only timeline strip, Bigger text,
# and the server's paragraph grouping on the edited clock.
# ---------------------------------------------------------------------------


class V2FrameTests(unittest.TestCase):
    """The page fits the window at laptop sizes; panels scroll
    inside themselves; no page scrollbar."""

    def test_no_page_scrollbar_at_laptop_sizes(self):
        self.assertIn("body.job-loaded { overflow: hidden; }", TEMPLATE)
        self.assertIn("height: 100dvh", TEMPLATE)
        # v0.11 (items 2 and 4): the stored widths are read
        # through clamps (--col-a / --col-c, defined on the split)
        # and the transcript keeps at least 320px, so the three
        # columns always fit the window with no sideways scroll.
        self.assertIn(
            "grid-template-columns: var(--col-a) 10px "
            "minmax(320px, 1fr) 10px var(--col-c);",
            TEMPLATE,
        )
        # 1280-wide windows get the narrower columns.
        self.assertIn("--pane-a: 320px; --pane-c: 280px;", TEMPLATE)
        # The transcript line is capped, never stretched.
        self.assertIn("max-width: 68ch", TEMPLATE)

    def test_top_bar_carries_the_three_labelled_buttons(self):
        head = between('class="app-head"', 'id="popupScrim"')
        self.assertIn('id="howItWorksToggle"', head)
        self.assertIn("How this works", head)
        self.assertIn('id="commentsToggle"', head)
        self.assertIn('id="commentsCount"', head)
        self.assertIn('id="settingsToggle"', head)
        self.assertIn('id="topJobName"', head)

    def test_comments_panel_closes_and_reopens_from_the_top_bar(self):
        self.assertIn('id="hideCommentsBtn"', TEMPLATE)
        self.assertIn("Hide comments", TEMPLATE)
        self.assertIn("function applyCommentsOpen(open)", TEMPLATE)
        self.assertIn('"tye-comments-open"', TEMPLATE)
        self.assertIn("body.comments-closed #toolsPane", TEMPLATE)

    def test_left_column_holds_only_the_watching_and_making_tools(self):
        left = between('class="review-left"', 'id="paneDivider"')
        order = [
            left.index('id="vid"'),
            left.index('id="fmtRow"'),
            left.index('id="tlbar"'),
            left.index('id="makeEditCard"'),
        ]
        self.assertEqual(order, sorted(order))
        # The money card and the keyboard list left the column.
        self.assertNotIn('id="moneyCard"', left)
        self.assertNotIn('id="shortcutLine"', left)


class V2PopupTests(unittest.TestCase):
    """Settings and How this works are pop ups over a dimmed page."""

    def test_popups_have_a_scrim_and_close_hooks(self):
        self.assertIn('id="popupScrim" hidden', TEMPLATE)
        self.assertIn('id="howItWorksPanel" hidden', TEMPLATE)
        self.assertIn("popup-panel", TEMPLATE)
        self.assertIn("function closePopups()", TEMPLATE)
        self.assertIn('e.key === "Escape"', TEMPLATE)
        self.assertIn(
            'popupScrim.addEventListener("click", closePopups)',
            TEMPLATE,
        )
        self.assertIn('id="settingsClose"', TEMPLATE)
        self.assertIn('id="howItWorksClose"', TEMPLATE)

    def test_keyboard_list_and_switch_live_in_how_this_works(self):
        panel = between('id="howItWorksPanel"', 'id="settingsPanel"')
        self.assertIn('id="shortcutLine"', panel)
        self.assertIn('id="shortcutToggle"', panel)

    def test_money_history_renders_inside_settings(self):
        panel = between('id="settingsPanel"', 'id="jobIncompleteBanner"')
        self.assertIn('id="moneyCard"', panel)
        self.assertIn('id="moneyBody"', panel)
        self.assertIn('id="moneyReset"', panel)
        self.assertIn('id="moneyStatus"', panel)
        self.assertIn('id="problemReportBtn"', panel)
        self.assertIn('id="problemReportNote"', panel)

    def test_money_details_link_opens_settings_at_money(self):
        self.assertIn('id="moneyDetailsLink"', TEMPLATE)
        self.assertIn("Money details", TEMPLATE)
        self.assertIn("function openSettingsAtMoney()", TEMPLATE)

    def test_cost_line_speaks_before_and_after_a_run(self):
        self.assertIn(
            'id="fdCostLine">Edits cost a few cents a run.', TEMPLATE)
        self.assertIn('"Your last edit cost "', TEMPLATE)

    def test_bigger_text_and_reset_layout_live_in_settings(self):
        panel = between('id="settingsPanel"', 'id="jobIncompleteBanner"')
        self.assertIn('id="biggerTextToggle"', panel)
        self.assertIn("Bigger text", panel)
        self.assertIn('id="resetLayoutBtn"', panel)
        self.assertIn("body.bigger-text", TEMPLATE)
        self.assertIn("font-size: 19px", TEMPLATE)


class V2TransportTests(unittest.TestCase):
    """One centered row; a word under every button; Play is the
    only blue button; the old Now-at readout is gone."""

    def test_words_under_every_button(self):
        row = between('id="fmtRow"', 'id="tlbar"')
        for label in (">Start<", ">Back 5<", ">Play<", ">Fwd 5<",
                      ">End<", ">Prev change<", ">Next change<"):
            self.assertIn(label, row)
        self.assertIn('class="tp-label"', row)
        self.assertIn(".tp-label { font-size: 11px;", TEMPLATE)

    def test_play_is_the_only_blue_button(self):
        self.assertIn(".tp-play { background: #0064d1;", TEMPLATE)
        self.assertIn('id="playPauseBtn"', TEMPLATE)

    def test_time_readout_is_the_edited_clock_pair(self):
        self.assertIn('id="clock">0:00 / 0:00<', TEMPLATE)
        self.assertIn("function transportReadout()", TEMPLATE)
        self.assertNotIn("Now at <b", TEMPLATE)


class V2TimelineTests(unittest.TestCase):
    """The strip is blocks only: no word ticks, no watched wash,
    no cut notches, held is not drawn."""

    def test_strip_is_blocks_and_dots_only(self):
        panel = between('id="tlbar"', 'id="fmtRow"') \
            if TEMPLATE.index('id="tlbar"') < TEMPLATE.index('id="fmtRow"') \
            else between('id="tlbar"', 'id="makeEditCard"')
        self.assertIn('id="tlRuler"', panel)
        self.assertIn('id="tlBlocks"', panel)
        self.assertIn('id="tlDots"', panel)
        self.assertIn('id="tlCaption"', panel)
        self.assertIn(".tl-block {", TEMPLATE)
        self.assertIn("background: #262b33", TEMPLATE)
        self.assertIn("border-radius: 8px", TEMPLATE)

    def test_captions_use_the_spec_words(self):
        self.assertIn(
            "Nothing taken out yet. This is your video as you "
            "dropped it.",
            TEMPLATE,
        )
        self.assertIn('"Edited: " + fmtTransport(out)', TEMPLATE)
        self.assertIn('" The dots are your comments."', TEMPLATE)

    def test_dots_follow_the_waiting_rule(self):
        # Round 2 refinement (Justin's add-on): a timeline dot
        # marks a spot still waiting on the person's call. Once
        # the spot is decided the dot goes away; it does not turn
        # gray. The transcript margin markers keep blue/gray.
        self.assertNotIn(".tl-dot.done", TEMPLATE)
        self.assertIn("function renderDots()", TEMPLATE)
        self.assertIn(
            'var waiting = g.items.filter(function (x) { return !x.done; });',
            TEMPLATE,
        )
        self.assertIn("if (!waiting.length) { return; }", TEMPLATE)
        self.assertIn("dot.className = \"tl-dot\";", TEMPLATE)
        self.assertIn(
            "count.textContent = waiting.length;", TEMPLATE)
        self.assertIn('.tx-comment-marker.done { background: #8e8e93; }',
                      TEMPLATE)


class V2TranscriptTests(unittest.TestCase):
    """Paragraphs, one gutter time each, the one help line."""

    def test_help_line_is_exact_and_instructions_are_gone(self):
        self.assertIn(
            "Times match the video you are watching.", TEMPLATE)
        self.assertNotIn(
            "The words of the edit, in order. Words that were cut "
            "are struck through and dimmed. Drag across words",
            TEMPLATE,
        )

    def test_paragraph_rendering_uses_the_server_grouping(self):
        self.assertIn("w.paragraph", TEMPLATE)
        self.assertIn('className = "tx-para"', TEMPLATE)
        self.assertIn('className = "tx-time"', TEMPLATE)
        self.assertIn("fmtTransport(et)", TEMPLATE)

    def test_double_click_pins_a_comment(self):
        self.assertIn('addEventListener("dblclick"', TEMPLATE)


class ParagraphIndexTests(unittest.TestCase):
    """Server: grouping computed once from the original word
    times (0.8s pause rule, speaker change, length break at a
    sentence end); entries carry the edited clock too."""

    def words(self, spec):
        out = []
        t = 0.0
        for text, gap in spec:
            t += gap
            out.append({"word": text, "start": round(t, 2),
                        "end": round(t + 0.3, 2)})
            t += 0.3
        return out

    def test_pause_of_point_eight_breaks(self):
        words = self.words([("One", 0.0), ("two.", 0.2),
                            ("Three", 0.9), ("four.", 0.2)])
        idx = webapp._paragraph_indices(words)
        self.assertEqual(idx, [0, 0, 1, 1])

    def test_short_pause_does_not_break(self):
        words = self.words([("One", 0.0), ("two.", 0.2),
                            ("Three", 0.5), ("four.", 0.2)])
        self.assertEqual(webapp._paragraph_indices(words), [0, 0, 0, 0])

    def test_speaker_change_breaks(self):
        words = self.words([("Hi.", 0.0), ("There.", 0.2)])
        words[1]["speaker"] = "Sam"
        words[0]["speaker"] = "Jo"
        self.assertEqual(webapp._paragraph_indices(words), [0, 1])

    def test_edited_time_maps_kept_and_cut(self):
        from talk_to_your_editor.timeline import Timeline
        words = self.words(
            [("a", 0.0), ("b", 0.2), ("c", 0.2), ("d", 0.2)])
        transcript = {"words": words, "duration": 10.0}
        cuts = {"kept_ranges": [{"start": 0.0, "end": 0.8},
                                {"start": 1.8, "end": 2.6}],
                "source_duration_s": 2.6}
        tl = Timeline(transcript, cuts)
        # A kept word keeps its offset; a cut word lands on the join.
        self.assertAlmostEqual(
            webapp._edited_time_for_source(tl, 0.2), 0.2)
        self.assertAlmostEqual(
            webapp._edited_time_for_source(tl, 1.2), 0.8)
        self.assertAlmostEqual(
            webapp._edited_time_for_source(tl, 2.0), 1.0)


class V2FrontDoorTests(unittest.TestCase):
    """6b: the staged preview stays small; the how-it-works
    details starts closed."""

    def test_drop_preview_is_capped(self):
        self.assertIn("#dropVideo { max-height: 200px;", TEMPLATE)

    def test_how_bringing_in_a_job_works_starts_closed(self):
        drop = between('id="dropCard"', 'id="dropVideoWrap"')
        self.assertIn('<details class="more-info">', drop)
        self.assertNotIn('<details class="more-info" open', drop)


class V2ArrangementTests(unittest.TestCase):
    """Arrangeable panels (Justin's add-on): swap sides, panels
    off and on, one panel wider, all remembered on this computer,
    with the one-screen fit kept in every arrangement."""

    def test_plain_controls_exist_in_panel_headers_and_top_bar(self):
        for marker in (
            'id="swapSidesBtn"', ">Swap sides<",
            'id="videoHideBtn"', ">Hide video<",
            'id="videoExpandBtn"', 'id="transcriptExpandBtn"',
            'id="commentsExpandBtn"', ">Make wider<",
            '"Back to normal"',
            'id="transcriptHideBtn"', ">Hide transcript<",
            'id="showVideoBtn"', ">Show video<",
            'id="showTranscriptBtn"', ">Show transcript<",
        ):
            self.assertIn(marker, TEMPLATE)

    def test_arrangement_is_remembered_in_the_same_storage_family(self):
        self.assertIn('"tye-layout"', TEMPLATE)
        self.assertIn("function applyLayout()", TEMPLATE)
        self.assertIn("function toggleExpandedPanel(name)", TEMPLATE)
        # Reset layout clears the arrangement with the sizes.
        self.assertIn('removeItem("tye-layout")', TEMPLATE)

    def test_swap_keeps_the_timeline_with_the_video_panel(self):
        # The timeline lives inside the video panel (.review-left),
        # so it follows the video wherever the arrangement puts it.
        left = between('class="review-left"', 'id="paneDivider"')
        self.assertIn('id="tlbar"', left)
        self.assertIn('id="vid"', left)
        self.assertIn("body.job-loaded.panels-swapped .review-left "
                      "{ grid-column: 3; }", TEMPLATE)
        self.assertIn("body.job-loaded.panels-swapped .review-right "
                      "{ grid-column: 1; }", TEMPLATE)

    def test_every_arrangement_keeps_the_one_screen_fit(self):
        for marker in (
            "body.job-loaded.video-hidden .review-left",
            "body.job-loaded.transcript-hidden .review-right",
            "body.job-loaded.expanded-video .review-split",
            "body.job-loaded.expanded-transcript .review-split",
            "body.job-loaded.expanded-comments .review-split",
            "grid-template-columns: minmax(0, 1fr);",
        ):
            self.assertIn(marker, TEMPLATE)
        # Expanding the comments panel reopens it first.
        self.assertIn(
            'if (layoutState.expanded === "comments") '
            '{ applyCommentsOpen(true); }',
            TEMPLATE,
        )


class V2FixASharpenedTests(unittest.TestCase):
    """FIX A, sharpened (Justin's add-on): after a run, the open
    page swaps the player to the new edited video on its own,
    through the state poll and applyJobState/syncPlayerToWatching,
    and the watching line is right at all times, mid-build too."""

    def test_building_state_is_named_plainly(self):
        self.assertIn("function showWatchingBuilding()", TEMPLATE)
        self.assertIn("Building your edited video", TEMPLATE)
        self.assertIn(
            "It will play here on its own when it is ready.",
            TEMPLATE,
        )
        # Building is a branch of the watching sync itself, so the
        # edited claim cannot print while the copy is not ready.
        sync = between(
            "function syncPlayerToWatching(job)",
            "// ---- What it costs",
        )
        self.assertIn("building = true;", sync)
        self.assertIn("showWatchingBuilding();", sync)
        self.assertIn('w.building ? "building" : "ready"', sync)

    def test_the_poll_applies_the_fresh_state_when_the_copy_lands(self):
        self.assertIn("applyJobState(fresh);", TEMPLATE)
        self.assertIn("syncPlayerToWatching(fresh);", TEMPLATE)
        # A transcript refresh also syncs the player, and the
        # render button re-reads the job state when it finishes.
        self.assertIn("syncPlayerToWatching(job);", TEMPLATE)
        self.assertIn("reloadJobState();", TEMPLATE)

    def test_the_watching_state_names_building_on_the_server(self):
        import inspect
        src = inspect.getsource(webapp._watching_state)
        self.assertIn('"building": False', src)
        self.assertIn('state["building"]', src)


if __name__ == "__main__":
    unittest.main()
