"""Tests for v0.15 Batch D (items 5, 6, 7, 9): teach, mic, and
the involvement pill.

Item 7: every dictation control is the microphone icon button
in the established treatment (38x38, radius 10, the app's mic
glyph, the name only in its title and aria-label); no button
in the app is labeled with the word "Dictate".

Item 5: one graduation-cap glyph everywhere teaching appears
(the top bar, the intake teach step, the teach card head), in
the app's stroke style like its neighbours. The research note
behind the choice lives at
v015-work/evidence/teach-icon-research.md.

Item 6: the intake pop-up carries one optional teach step,
offered only while the creator has no saved rules yet; Skip
for now dismisses it in one tap and it never blocks bringing
the parts in.

Item 9: both involvement controls (front door and intake) are
ONE two-segment pill each, "Proactive" and "Leave more in",
the chosen segment filled with the one blue and set heavier,
with the plain-words summary line beneath. The server's mode
values stay "cleanest" and "tailor".
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src/talk_to_your_editor/templates/test_app.html").read_text()

MIC_RECT = '<rect x="9" y="3" width="6" height="11" rx="3"'
MIC_ARC = 'd="M5.5 11.5a6.5 6.5 0 0 0 13 0"'
DICTATE_IDS = (
    "intakeDictate", "fdDictate",
    "editNoteSpeak", "draftDictate",
)

# v0.15 Batch E1: whyDictate is built inside the thread's
# why entry (same mic treatment, wired after render), and
# overallDictate left with the absorbed overall box.
DYNAMIC_DICTATE_IDS = ("whyDictate",)


def button_tag(button_id):
    m = re.search(
        r'<button[^>]*id="%s".*?</button>' % button_id, TEMPLATE, re.S)
    assert m, button_id
    return m.group(0)


def visible_text(tag):
    inner = tag[tag.index(">") + 1:tag.rindex("</button>")]
    return re.sub(r"<[^>]+>", "", inner).strip()


def rule_values(selector_pattern):
    m = re.search(selector_pattern + r"\s*\{([^}]*)\}", TEMPLATE)
    assert m, selector_pattern
    out = {}
    for part in m.group(1).split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def svg_inner(tag):
    m = re.search(r"<svg[^>]*>(.*?)</svg>", tag, re.S)
    assert m, tag[:60]
    return m.group(1)


class DictateIconTests(unittest.TestCase):
    def test_every_dictation_button_is_the_mic_icon_button(self):
        for button_id in DICTATE_IDS:
            tag = button_tag(button_id)
            self.assertEqual(visible_text(tag), "", button_id)
            self.assertIn('title="Dictate"', tag, button_id)
            self.assertIn('aria-label="Dictate"', tag, button_id)
            self.assertIn(MIC_RECT, tag, button_id)
            self.assertIn(MIC_ARC, tag, button_id)
        # The thread's why mic is built in code with the same
        # glyph and name.
        for button_id in DYNAMIC_DICTATE_IDS:
            at = TEMPLATE.index('mic.id = "%s";' % button_id)
            seg = TEMPLATE[at:at + 700]
            self.assertIn('mic.title = "Dictate";', seg, button_id)
            self.assertIn(MIC_RECT, seg, button_id)
            self.assertIn(MIC_ARC, seg, button_id)
        self.assertNotIn('id="overallDictate"', TEMPLATE)

    def test_no_button_is_labeled_dictate(self):
        for m in re.finditer(r"<button[^>]*>.*?</button>",
                             TEMPLATE, re.S):
            self.assertNotIn(
                "Dictate", visible_text(m.group(0)), m.group(0)[:80])

    def test_the_icon_treatment_is_the_established_one(self):
        # Same shape as the composer's button: 38x38, radius 10,
        # 20px glyph. v0.17 (feedback item 19): the button no
        # longer grows into a pill while it records or writes;
        # a voice level meter beside it carries the feedback.
        rule = rule_values(r"\.dictate-btn")
        self.assertEqual(rule["width"], "38px")
        self.assertEqual(rule["height"], "38px")
        self.assertEqual(rule["min-height"], "38px")
        self.assertEqual(rule["padding"], "0")
        self.assertEqual(rule["border-radius"], "10px")
        glyph = rule_values(r"\.dictate-btn svg")
        self.assertEqual(glyph["width"], "20px")
        self.assertEqual(glyph["height"], "20px")
        self.assertNotIn(
            ".dictate-btn.recording, .dictate-btn.processing",
            TEMPLATE)
        self.assertIn(".dictate-meter", TEMPLATE)
        # Inside the edit pop-up the pop-up's small-button rule
        # does not pad the icon back down.
        self.assertIn("#editPopup button.dictate-btn", TEMPLATE)
        # Inside the More card the card's 15px inline-icon rule
        # does not shrink the overall note's mic glyph either.
        self.assertIn("#moreCard button.dictate-btn svg", TEMPLATE)

    def test_dictation_wiring_and_status_spans_are_unchanged(self):
        for btn, area, status in (
            ("fdDictate", "fdDirection", "fdDictateStatus"),
            ("draftDictate", "draft", "draftDictateStatus"),
            ("intakeDictate", "intakeDirection", "firstEditStatus"),
            ("whyDictate", "whyNote", "whyDictateStatus"),
            ("editNoteSpeak", "editNoteText", "editNoteStatus"),
        ):
            self.assertIn(
                'wireDictation("%s", "%s", "%s")' % (btn, area, status),
                TEMPLATE)


class TeachIconTests(unittest.TestCase):
    def test_one_cap_glyph_everywhere_teaching_appears(self):
        top = svg_inner(button_tag("teachToggle"))
        # v0.17 (feedback items 10 and 11): the teach step is
        # out of the intake pop-up; the cap now also rides the
        # first page's Train the editor button.
        home = svg_inner(button_tag("trainHomeBtn"))
        head = svg_inner(TEMPLATE[
            TEMPLATE.index('id="teachFull"'):
            TEMPLATE.index('id="teachFull"') + 400])
        self.assertEqual(top, home)
        self.assertEqual(top, head)
        # A graduation cap: the board, the head band, the tassel.
        self.assertIn('d="M12 4L2.5 8.5 12 13l9.5-4.5L12 4z"', top)
        self.assertIn('d="M21.5 8.5V14"', top)
        # In the app's stroke style, like its neighbours.
        self.assertIn('stroke-width="1.8"', button_tag("teachToggle"))


class IntakeTeachStepTests(unittest.TestCase):
    """v0.17 (feedback items 10, 11, 12): the teach step is
    OUT of the intake pop-up. Training lives on the first
    page ("Train the editor") and in the teach card; the
    pop-up is only for bringing in a video."""

    def test_the_teach_step_is_gone_from_the_intake_popup(self):
        self.assertNotIn('id="intakeTeachStep"', TEMPLATE)
        self.assertNotIn("intakeTeachBtn", TEMPLATE)
        self.assertNotIn("intakeTeachSkip", TEMPLATE)
        self.assertNotIn("refreshIntakeTeachStep", TEMPLATE)
        self.assertNotIn("Teach the app", TEMPLATE)

    def test_train_the_editor_lives_on_the_first_page(self):
        self.assertIn('id="trainHomeBtn"', TEMPLATE)
        self.assertIn("function openTeachCard()", TEMPLATE)
        body = TEMPLATE[
            TEMPLATE.index("function openTeachCard()"):
            TEMPLATE.index("function openTeachCard()") + 320]
        self.assertIn("closeIntakePopup();", body)
        self.assertIn('getElementById("teachToggle")', body)
        # The first page's button runs through the helper;
        # the helper opens the card through the top bar's
        # own toggle.
        self.assertIn('trainHomeBtn.addEventListener('
                      '"click", openTeachCard);', TEMPLATE)
        self.assertIn('teachToggle.addEventListener('
                      '"click", function ()', TEMPLATE)

    def test_nothing_about_training_gates_bring_these_in(self):
        self.assertNotIn("intakeTeach", TEMPLATE[
            TEMPLATE.index("intakeContinueBtn.addEventListener"):])


class InvolvementPillTests(unittest.TestCase):
    def pill_block(self, pill_id):
        start = TEMPLATE.index('id="%s"' % pill_id)
        end = TEMPLATE.index("</div>", start)
        return TEMPLATE[start:end]

    def test_both_controls_are_one_two_segment_pill(self):
        for pill_id in ("fdModePills", "inModePills"):
            block = self.pill_block(pill_id)
            self.assertIn("mode-pill", TEMPLATE[
                TEMPLATE.index('id="%s"' % pill_id) - 60:
                TEMPLATE.index('id="%s"' % pill_id)])
            self.assertEqual(block.count('class="mode-seg"'), 2)
            self.assertIn(
                'data-mode="cleanest" aria-pressed="true">Proactive<',
                block)
            self.assertIn(
                'data-mode="tailor" aria-pressed="false">Leave more in<',
                block)
            # The old loose-button names are gone from the page.
        self.assertNotIn("Cleanest cut", TEMPLATE)
        self.assertNotIn("Tailor it with me</button>", TEMPLATE)

    def test_the_summary_lines_map_to_the_same_two_modes(self):
        self.assertIn(
            'cleanest: "The editor finishes everything it is sure '
            'about, and strikes repeats it detects."', TEMPLATE)
        self.assertIn(
            'tailor: "More is left in, and the editor asks before '
            'taking things out. Retakes it detects are cut in '
            'both ways of working, and anything cut can come '
            'back."', TEMPLATE)
        # The notes still land under each pill.
        self.assertIn('id="fdModeNote"', TEMPLATE)
        self.assertIn('id="inModeNote"', TEMPLATE)
        # The collapsed row summaries use the pill's own name.
        self.assertEqual(
            TEMPLATE.count("Proactive (recommended)"), 2)

    def test_pill_geometry_and_the_chosen_segment(self):
        pill = rule_values(r"\.mode-pill")
        self.assertEqual(pill["gap"], "0")
        seg = rule_values(r"\.mode-pill \.mode-seg")
        self.assertEqual(seg["height"], "48px")
        self.assertEqual(seg["min-height"], "48px")
        self.assertEqual(seg["font-size"], "16px")
        first = rule_values(r"\.mode-pill \.mode-seg:first-child")
        self.assertEqual(first["border-radius"], "999px 0 0 999px")
        last = rule_values(r"\.mode-pill \.mode-seg:last-child")
        self.assertEqual(last["border-radius"], "0 999px 999px 0")
        # The chosen segment reads without colour alone: the one
        # blue fill AND the heavier label weight.
        on = rule_values(r"\.mode-pill \.mode-seg\.on")
        self.assertEqual(on["background"], "var(--blue)")
        self.assertEqual(on["font-weight"], "700")
        self.assertEqual(seg["font-weight"], "600")

    def test_the_setters_still_record_cleanest_and_tailor(self):
        for fn, var in (("setFdMode", "fdMode"), ("setInMode", "inMode")):
            block = TEMPLATE[TEMPLATE.index("function %s" % fn):]
            block = block[:block.index("\n  }\n")]
            self.assertIn('mode !== "tailor"', block)
            self.assertIn('mode = "cleanest"', block)
            self.assertIn('%s = mode;' % var, block)
            # The pressed state follows the chosen segment.
            self.assertIn('setAttribute("aria-pressed"', block)
        # And the recorded values are what the runs send.
        self.assertIn("mode: fdMode,", TEMPLATE)
        self.assertIn("mode: inMode,", TEMPLATE)


if __name__ == "__main__":
    unittest.main()
