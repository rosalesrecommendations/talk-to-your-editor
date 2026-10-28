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
        # Four intake rows (retakes, silence, fillers, mode) and
        # five review rows (those four plus formats/direction).
        self.assertEqual(TEMPLATE.count('class="tool-options" hidden'), 9)
        self.assertEqual(TEMPLATE.count('class="tool-open"'), 9)

    def test_rows_open_pre_set_to_the_recommended_choice(self):
        self.assertIn("Last take stays (recommended)", TEMPLATE)
        self.assertIn("Leave 0.2 seconds (recommended)", TEMPLATE)
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


if __name__ == "__main__":
    unittest.main()
