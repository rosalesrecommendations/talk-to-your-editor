"""Framing: how a video is cropped, and where the face sits in it.

The teaching routes learn how a creator edits from words. This
module learns the picture side from a finished video: whether it
is zoomed in, and where the face sits on the screen, so a later
render can be cut to a different shape (9:16 or 16:9) with the
face landing in the same place at a similar size.

Detection is YuNet (the face_detection_yunet_2023mar.onnx model
from the OpenCV model zoo, MIT licensed, bundled under assets/),
run locally through onnxruntime. Nothing is sent anywhere, and
there is no model call and no cost. onnxruntime and numpy ride
along with the app's speech extra (faster-whisper); on an install
without them, framing reads as unavailable, in plain words, and
nothing about framing is saved or claimed.

The decode below follows the model's published output shape:
three strides (8, 16, 32); a location's score is its class score
times its objectness; the box center is the location's center
plus the predicted offset times the stride; the box size is the
stride times exp of the predicted log size; standard NMS at 0.3.
It was verified in this repo's build room against a real face
photo: the decoded keypoints land on the eyes, nose, and mouth,
and the box frames the face forehead to chin.

Everything except the detection itself is pure functions of
boxes and sizes, so the profile and crop math is testable
without a camera, a model, or a video.

Honest limits: one face per frame counts (the biggest one; this
app is for one person talking to a camera). A frame with no face
found is skipped, never guessed at. "Zoom" here means how large
the face sits in the frame; whether the camera itself was zoomed
or the video is a crop of a bigger frame cannot be known from
the video alone, and this module never claims otherwise.
"""

from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path
from typing import Optional

from . import transcribe as speech

MODEL_FILENAME = "face_detection_yunet_2023mar.onnx"
MODEL_ENV_VAR = "TYE_FACE_MODEL"
INPUT_SIZE = 640
STRIDES = (8, 16, 32)
SCORE_THRESHOLD = 0.6
NMS_THRESHOLD = 0.3
SAMPLE_COUNT = 7
# The one ruler for face shares that get compared with each
# other (a reference against a rendered output, on Finish):
# every such read is a spread of this many samples, median
# share, through ruler_profile below. Reads taken with
# different sample sets disagree on size (see ruler_profile),
# so a size comparison is only honest when both sides were
# read this one way.
RULER_SAMPLE_COUNT = 21

ASPECTS = {"9:16": 9.0 / 16.0, "16:9": 16.0 / 9.0}


class FramingUnavailable(RuntimeError):
    """The face-spotting pieces are not on this computer."""


# ---------------------------------------------------------------------------
# The model file and the runtime
# ---------------------------------------------------------------------------


def find_model(explicit: Optional[Path] = None) -> Optional[Path]:
    """The YuNet model file, or None when it cannot be found."""
    candidates = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env = os.environ.get(MODEL_ENV_VAR)
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).parent / "assets" / MODEL_FILENAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _runtime():
    """(numpy, onnxruntime), or raise FramingUnavailable."""
    try:
        import numpy
    except ImportError:
        raise FramingUnavailable(
            "numpy is not installed on this computer"
        )
    try:
        import onnxruntime
    except ImportError:
        raise FramingUnavailable(
            "onnxruntime is not installed on this computer"
        )
    return numpy, onnxruntime


def detection_status() -> tuple:
    """(True, "") when framing can be read here, else (False,
    the plain reason it cannot)."""
    try:
        _runtime()
    except FramingUnavailable as exc:
        return False, str(exc)
    if find_model() is None:
        return False, "the face-spotting model file is missing"
    return True, ""


# ---------------------------------------------------------------------------
# Detection: frames in, face boxes out (the only impure part)
# ---------------------------------------------------------------------------


def decode_detections(outputs: dict, numpy) -> list:
    """YuNet outputs -> face boxes in 640x640 input pixels.

    ``outputs`` maps the model's output names to its arrays.
    Returns [(x1, y1, x2, y2, score)], best score first, after
    NMS. Pure given the arrays, so tests can drive it directly.
    """
    np = numpy
    boxes = []
    scores = []
    for stride in STRIDES:
        cls = outputs[f"cls_{stride}"][0, :, 0]
        obj = outputs[f"obj_{stride}"][0, :, 0]
        bbox = outputs[f"bbox_{stride}"][0]
        score = cls * obj
        grid = INPUT_SIZE // stride
        for index in np.where(score > SCORE_THRESHOLD)[0]:
            gy, gx = divmod(int(index), grid)
            cx = (gx + 0.5) * stride
            cy = (gy + 0.5) * stride
            dx, dy, dw, dh = (float(v) for v in bbox[index])
            center_x = cx + dx * stride
            center_y = cy + dy * stride
            width = stride * math.exp(dw)
            height = stride * math.exp(dh)
            boxes.append(
                (
                    center_x - width / 2,
                    center_y - height / 2,
                    center_x + width / 2,
                    center_y + height / 2,
                )
            )
            scores.append(float(score[index]))
    if not boxes:
        return []
    boxes_np = np.array(boxes, dtype=float)
    scores_np = np.array(scores, dtype=float)
    order = list(np.argsort(scores_np)[::-1])
    keep = []
    while order:
        best = order[0]
        keep.append(best)
        rest = order[1:]
        if not rest:
            break
        x1 = np.maximum(boxes_np[best][0], boxes_np[rest, 0])
        y1 = np.maximum(boxes_np[best][1], boxes_np[rest, 1])
        x2 = np.minimum(boxes_np[best][2], boxes_np[rest, 2])
        y2 = np.minimum(boxes_np[best][3], boxes_np[rest, 3])
        inter = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        areas = (boxes_np[:, 2] - boxes_np[:, 0]) * (
            boxes_np[:, 3] - boxes_np[:, 1]
        )
        iou = inter / (areas[best] + areas[rest] - inter)
        order = [r for r, v in zip(rest, iou) if v <= NMS_THRESHOLD]
    return [
        (
            float(boxes_np[i][0]),
            float(boxes_np[i][1]),
            float(boxes_np[i][2]),
            float(boxes_np[i][3]),
            float(scores_np[i]),
        )
        for i in keep
    ]


class _Detector:
    """One loaded YuNet session, reused across frames."""

    def __init__(self, model_path: Path):
        np, ort = _runtime()
        self._np = np
        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self._names = [o.name for o in self._session.get_outputs()]

    def detect(self, rgb: bytes, width: int, height: int) -> list:
        """Face boxes in the frame's own pixels, best first.

        The frame must be the model's own 640x640: the model
        stretches whatever it is fed to that size, so callers
        hand it frames already extracted at 640x640 and scale
        the boxes back to the source's pixels themselves.
        """
        np = self._np
        frame = np.frombuffer(rgb, dtype=np.uint8).reshape(
            height, width, 3
        )
        # The model takes BGR.
        blob = (
            frame[:, :, ::-1]
            .transpose(2, 0, 1)[None]
            .astype(np.float32)
        )
        outputs = self._session.run(None, {"input": blob})
        return decode_detections(
            dict(zip(self._names, outputs)), np
        )


def _frame_rgb(
    video: Path, at_s: float, width: int, height: int
) -> Optional[bytes]:
    """One frame of a video as raw RGB at the given size."""
    ffmpeg = "ffmpeg"
    command = [
        ffmpeg, "-y", "-v", "error",
        "-ss", f"{at_s:.3f}", "-i", str(video),
        "-frames:v", "1",
        "-vf", f"scale={width}:{height}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    expected = width * height * 3
    if len(proc.stdout) != expected:
        return None
    return proc.stdout


def kept_range_times(ranges: list, sample_count: int = SAMPLE_COUNT) -> list:
    """Sample times spread across a plan's kept ranges.

    The framing of a new video is read from its KEPT footage
    (brief 3): the parts the edit actually shows. Times are
    spread evenly over the kept timeline and mapped back to
    source times, so a long cut stretch cannot pull the median
    face toward footage nobody will see.
    """
    spans = [
        (float(r["start"]), float(r["end"]))
        for r in ranges or []
        if float(r["end"]) > float(r["start"])
    ]
    if not spans:
        return []
    total = sum(end - start for start, end in spans)
    times = []
    for i in range(sample_count):
        at = total * (i + 0.5) / sample_count
        walked = 0.0
        for start, end in spans:
            if walked + (end - start) >= at:
                times.append(start + (at - walked))
                break
            walked += end - start
        else:
            times.append(spans[-1][1] - 0.05)
    return times


def sample_faces(
    video: Path, detector=None, sample_count: int = SAMPLE_COUNT,
    times: Optional[list] = None,
) -> tuple:
    """Sample frames across a video and find the faces in each.

    Returns (width, height, per-frame face lists) in the source's
    own pixels. ``detector`` is the seam tests stub: a callable
    (rgb_bytes, width, height) -> [(x1, y1, x2, y2, score)].
    ``times``, when given, are the exact source times to sample
    (the kept footage, for a new video's framing match).
    Raises FramingUnavailable when the pieces are missing, and
    speech.TranscribeError when the file cannot be probed.
    """
    info = speech.probe_media(video)
    src_w = info.get("width")
    src_h = info.get("height")
    duration = info.get("duration")
    if not src_w or not src_h:
        raise speech.TranscribeError(
            "That file has no picture track to read framing from."
        )
    if detector is None:
        model = find_model()
        if model is None:
            raise FramingUnavailable(
                "the face-spotting model file is missing"
            )
        session = _Detector(model)
        detector = session.detect
    frames = []
    if times:
        times = [float(t) for t in times]
    elif duration:
        times = [
            duration * (i + 0.5) / sample_count
            for i in range(sample_count)
        ]
    else:
        times = [0.0]
    # Frames are extracted at the model's own 640x640 (it
    # stretches its input to that size anyway); detected boxes
    # scale back to the source's pixels per axis.
    back_x = int(src_w) / INPUT_SIZE
    back_y = int(src_h) / INPUT_SIZE
    for at_s in times:
        rgb = _frame_rgb(video, at_s, INPUT_SIZE, INPUT_SIZE)
        if rgb is None:
            frames.append([])
            continue
        frames.append(
            [
                (x1 * back_x, y1 * back_y, x2 * back_x, y2 * back_y, s)
                for (x1, y1, x2, y2, s) in detector(
                    rgb, INPUT_SIZE, INPUT_SIZE
                )
            ]
        )
    return int(src_w), int(src_h), frames


# ---------------------------------------------------------------------------
# The profile: pure math over detected boxes
# ---------------------------------------------------------------------------


def _median(values: list) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def build_profile(
    source_width: int, source_height: int, frames: list
) -> dict:
    """A framing profile from per-frame face boxes, in pixels.

    Each frame contributes its biggest face (one person talking
    to a camera is this app's whole world). Frames with no face
    are ignored. With no face anywhere, the profile says so and
    carries no numbers: framing is never made up.
    """
    centers_x, centers_y, widths, heights = [], [], [], []
    for faces in frames:
        if not faces:
            continue
        x1, y1, x2, y2 = max(
            (tuple(map(float, box[:4])) for box in faces),
            key=lambda b: (b[2] - b[0]) * (b[3] - b[1]),
        )
        centers_x.append(((x1 + x2) / 2) / source_width)
        centers_y.append(((y1 + y2) / 2) / source_height)
        widths.append((x2 - x1) / source_width)
        heights.append((y2 - y1) / source_height)
    if not centers_x:
        return {
            "found": False,
            "source_width": int(source_width),
            "source_height": int(source_height),
            "frames_sampled": len(frames),
            "frames_with_face": 0,
        }
    return {
        "found": True,
        "source_width": int(source_width),
        "source_height": int(source_height),
        "source_aspect": source_width / source_height,
        "face": {
            "center_x": _median(centers_x),
            "center_y": _median(centers_y),
            "width": _median(widths),
            "height": _median(heights),
        },
        # "Zoom", in the only sense a finished video can show:
        # how much of the frame's width the face takes up.
        "zoom": _median(widths),
        "frames_sampled": len(frames),
        "frames_with_face": len(centers_x),
        "detector": "yunet-2023mar",
    }


def profile_from_video(
    video: Path, detector=None, sample_count: int = SAMPLE_COUNT,
    times: Optional[list] = None,
) -> dict:
    """A finished video's framing profile, or a plain no.

    Returns the build_profile dict with a "status" added: "ok"
    when a face was found, "no_face" when the video was read and
    no face showed up, "unavailable" when this computer cannot
    spot faces (the note says why, in plain words).
    """
    try:
        width, height, frames = sample_faces(
            video, detector=detector, sample_count=sample_count,
            times=times,
        )
    except FramingUnavailable as exc:
        return {
            "status": "unavailable",
            "found": False,
            "note": (
                "Framing could not be read on this computer "
                f"({exc}), so no framing was saved."
            ),
        }
    except speech.TranscribeError as exc:
        return {
            "status": "unavailable",
            "found": False,
            "note": f"Framing could not be read: {exc}",
        }
    profile = build_profile(width, height, frames)
    if not profile.get("found"):
        profile["status"] = "no_face"
        profile["note"] = (
            "No face was found in the finished video, so no "
            "framing was saved."
        )
        return profile
    profile["status"] = "ok"
    return profile


def ruler_profile(
    video: Path, times: Optional[list] = None, detector=None
) -> dict:
    """A face profile read with THE one ruler.

    Every face share the Finish screen compares (the reference
    learned on Train, the new video's own face, and a rendered
    output being checked against its reference) is read this
    one way: the same detector and settings, a spread of
    RULER_SAMPLE_COUNT samples across the video (or across the
    plan's kept footage, when ``times`` hands the kept moments
    in), and the median share. Two reads taken any other way
    disagree on size far more than on position: YuNet's box
    shifts with the face's scale in frame, and a talking face
    changes size through the video, so different sample sets
    give different medians. One ruler is what makes a size
    comparison honest.
    """
    return profile_from_video(
        video,
        detector=detector,
        sample_count=RULER_SAMPLE_COUNT,
        times=times,
    )


# ---------------------------------------------------------------------------
# Saying it plainly, and cutting a new shape around the face
# ---------------------------------------------------------------------------


def _position_word(center_x: float) -> str:
    if center_x < 0.42:
        return "left of center"
    if center_x > 0.58:
        return "right of center"
    return "center frame"


def _height_word(center_y: float) -> str:
    if center_y < 0.36:
        return "high in the frame"
    if center_y > 0.62:
        return "low in the frame"
    return ""


def _closeness_word(width_fraction: float) -> str:
    if width_fraction >= 0.33:
        return "fairly close up"
    if width_fraction >= 0.20:
        return "a medium shot"
    return "fairly wide"


def framing_sentence(profile: dict) -> str:
    """The one plain line a teach summary gains, or "" when the
    profile holds no face."""
    if not profile or not profile.get("found"):
        return ""
    face = profile["face"]
    position = _position_word(float(face["center_x"]))
    height = _height_word(float(face["center_y"]))
    closeness = _closeness_word(float(face["width"]))
    if height:
        return (
            f"Your face sits {position} and {height}, "
            f"{closeness}."
        )
    return f"Your face sits {position}, {closeness}."


def aspect_value(aspect) -> float:
    """An aspect given as "9:16", "16:9", or a number, as w/h."""
    if isinstance(aspect, str):
        if aspect in ASPECTS:
            return ASPECTS[aspect]
        if ":" in aspect:
            w, h = aspect.split(":", 1)
            return float(w) / float(h)
        return float(aspect)
    return float(aspect)


def crop_window(
    profile: Optional[dict], source_width: int, source_height: int,
    aspect,
) -> dict:
    """The crop window for one output aspect, as fractions.

    The window is the largest rectangle of the target aspect
    that fits the source, which is also the choice that keeps
    the face's size closest to the reference (a smaller window
    only ever makes the face bigger). It is positioned so the
    reference face's center lands at the same share of the
    frame, clamped inside the picture. With no profile face,
    the window is centered. Returns fractions plus where the
    face lands and how large it sits in the output, so a
    caller can say honestly what was done.
    """
    target = aspect_value(aspect)
    source_aspect = source_width / source_height
    if target <= source_aspect:
        win_h = 1.0
        win_w = target / source_aspect
    else:
        win_w = 1.0
        win_h = source_aspect / target
    face = None
    if profile and profile.get("found"):
        face = profile["face"]
        # Place the window so the face center keeps the same
        # share of the frame it had in the reference: at
        # (cx - x) / win_w == cx, i.e. x = cx * (1 - win_w).
        want_x = float(face["center_x"]) * (1.0 - win_w)
        want_y = float(face["center_y"]) * (1.0 - win_h)
    else:
        want_x = (1.0 - win_w) / 2
        want_y = (1.0 - win_h) / 2
    x = min(max(want_x, 0.0), 1.0 - win_w)
    y = min(max(want_y, 0.0), 1.0 - win_h)
    result = {"x": x, "y": y, "width": win_w, "height": win_h}
    if face is not None:
        result["face_center_x"] = (
            float(face["center_x"]) - x
        ) / win_w
        result["face_center_y"] = (
            float(face["center_y"]) - y
        ) / win_h
        result["face_width"] = float(face["width"]) / win_w
    return result


def crop_pixels(
    window: dict, source_width: int, source_height: int
) -> tuple:
    """A fractional window as even ffmpeg pixel numbers.

    (x, y, w, h), all even (yuv420p needs it), inside the
    source, at least 2x2.
    """
    def even(value: float) -> int:
        return max(2, int(round(value)) // 2 * 2)

    w = min(even(window["width"] * source_width), even(source_width))
    h = min(even(window["height"] * source_height), even(source_height))
    x = even(window["x"] * source_width)
    y = even(window["y"] * source_height)
    x = min(x, source_width - w)
    y = min(y, source_height - h)
    return max(0, x), max(0, y), w, h


def full_frame_size(
    aspect, source_width: int, source_height: int
) -> tuple:
    """The frame a finished export of this shape fills.

    A full frame at the source's own quality, capped at 4K on
    the long side (Justin's rule, set with the finish fixes):
    16:9 from a 3840x2160 source is the source's own
    3840x2160, and 9:16 from the same source is that same
    frame turned on its side, 2160x3840, full portrait 4K.
    In both cases the crop is scaled up to fill the frame; the
    export is never the crop's own smaller size. Sizes are
    even, because an H.264 yuv420p frame cannot carry an odd
    width or height.
    """

    def even(value: float) -> int:
        return max(2, int(round(value / 2)) * 2)

    long_side = even(
        min(max(int(source_width), int(source_height)), 3840)
    )
    short_side = even(long_side * 9 / 16)
    if aspect_value(aspect) >= 1.0:
        return long_side, short_side
    return short_side, long_side


def match_window(
    ref_profile: Optional[dict],
    new_profile: Optional[dict],
    source_width: int,
    source_height: int,
    aspect,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> dict:
    """The crop that makes the new video's face match a reference.

    This is the Message 3 framing (brief 3): the reference of one
    shape teaches where the face sits in that shape's frame and
    how much of the frame's width it takes up (``ref_profile``,
    learned on Train from a finished video of that shape). The
    face is then found in the NEW video (``new_profile``, sampled
    from the new video itself), and the crop is sized and placed
    from the new face:

    - crop width = the new face's width / the reference face
      width share, so the face takes up the same share of the
      output frame as it does in the reference;
    - crop height follows the shape;
    - the crop is positioned so the face lands at the reference's
      center x and y shares, clamped inside the picture;
    - ``offset_x`` / ``offset_y`` are the creator's per-shape
      nudge, in shares of the output frame.

    Honest limits, named in ``notes`` rather than hidden: with no
    reference of this shape yet, or no face found in the new
    video, the window is the widest centered crop of the shape
    and ``matched`` is False, so no caller can print a match line
    for it. When the source is too tight to zoom out as far as
    the reference asks (the face already fills more of the source
    than the reference share), the widest crop that fits is used
    and the note says so. When the crop is small enough that
    enlarging it to full size may look soft, the note says that
    too. ``predicted`` carries where the face will land (center
    shares and width share of the output), computed from the
    window actually chosen, clamping included.
    """
    target = aspect_value(aspect)
    source_aspect = source_width / source_height
    # The widest window of this aspect that fits the source.
    if target <= source_aspect:
        max_w_frac, max_h_frac = target / source_aspect, 1.0
    else:
        max_w_frac, max_h_frac = 1.0, source_aspect / target

    def centered() -> dict:
        return {
            "x": (1.0 - max_w_frac) / 2,
            "y": (1.0 - max_h_frac) / 2,
            "width": max_w_frac,
            "height": max_h_frac,
        }

    ref_face = None
    if ref_profile and ref_profile.get("found"):
        ref_face = ref_profile["face"]
    if ref_face is None:
        window = centered()
        window.update(
            {
                "matched": False,
                "notes": ["no_reference"],
                "predicted": None,
            }
        )
        return window
    new_face = None
    if new_profile and new_profile.get("found"):
        new_face = new_profile["face"]
    if new_face is None:
        window = centered()
        window.update(
            {
                "matched": False,
                "notes": ["no_face_in_new"],
                "predicted": None,
            }
        )
        return window

    notes = []
    face_w_px = float(new_face["width"]) * source_width
    crop_w_px = face_w_px / float(ref_face["width"])
    crop_h_px = crop_w_px / target
    max_w_px = max_w_frac * source_width
    max_h_px = max_h_frac * source_height
    if crop_w_px > max_w_px or crop_h_px > max_h_px:
        # Too tight to zoom out that far: the widest crop wins.
        crop_w_px, crop_h_px = max_w_px, max_h_px
        notes.append("too_tight")
    # The export fills the shape's full frame for the source;
    # a crop that has to scale up by more than half again to
    # fill it may look soft, and the note says so.
    frame_w, _frame_h = full_frame_size(
        aspect, source_width, source_height
    )
    if crop_w_px > 0 and frame_w / crop_w_px > 1.5:
        notes.append("may_look_soft")
    face_cx_px = float(new_face["center_x"]) * source_width
    face_cy_px = float(new_face["center_y"]) * source_height
    x_px = face_cx_px - float(ref_face["center_x"]) * crop_w_px
    y_px = face_cy_px - float(ref_face["center_y"]) * crop_h_px
    x_px += float(offset_x) * crop_w_px
    y_px += float(offset_y) * crop_h_px
    x_px = min(max(x_px, 0.0), source_width - crop_w_px)
    y_px = min(max(y_px, 0.0), source_height - crop_h_px)
    window = {
        "x": x_px / source_width,
        "y": y_px / source_height,
        "width": crop_w_px / source_width,
        "height": crop_h_px / source_height,
        "matched": True,
        "notes": notes,
        "predicted": {
            "center_x": (face_cx_px - x_px) / crop_w_px,
            "center_y": (face_cy_px - y_px) / crop_h_px,
            "width": face_w_px / crop_w_px,
        },
    }
    return window


def scale_window(
    window: dict,
    ref_profile: dict,
    new_profile: dict,
    source_width: int,
    source_height: int,
    aspect,
    measured_share: float,
) -> dict:
    """A matched window re-sized by a measured correction.

    The render loop's correction (Message 3 fix): a rendered
    output is measured with the one ruler, and when its face
    width share misses the reference's, the crop is corrected
    and the shape rendered again. The share scales inversely
    with the crop's width, so the crop is multiplied by the
    measured share over the reference share, which divides the
    share by the same factor and lands it on the reference.
    The window is re-placed so the face keeps the center shares
    the matched window predicted (the matched face center),
    clamped inside the picture. When the corrected crop would
    be wider than the widest crop of the shape that fits, the
    widest crop is used and the note says too_tight, exactly as
    in match_window; the soft note is recomputed for the new
    crop, and "size_corrected" records that this window came
    from a measurement, not the first plan.

    ``predicted`` is honest about which read it comes from:
    the centers are placed from the new video's own face read
    (position reads agree well), while the width is the
    measured face width from the render just checked (the
    measured share times the old crop), divided by the new
    crop: the size the next ruler read is expected to see,
    which is the reference share unless the widest-crop clamp
    intervened.
    """
    ref_share = float(ref_profile["face"]["width"])
    ratio = float(measured_share) / ref_share
    target = aspect_value(aspect)
    source_aspect = source_width / source_height
    if target <= source_aspect:
        max_w_frac, max_h_frac = target / source_aspect, 1.0
    else:
        max_w_frac, max_h_frac = 1.0, source_aspect / target
    max_w_px = max_w_frac * source_width
    max_h_px = max_h_frac * source_height
    old_crop_w_px = float(window["width"]) * source_width
    measured_face_w_px = float(measured_share) * old_crop_w_px
    crop_w_px = old_crop_w_px * ratio
    crop_h_px = crop_w_px / target
    notes = []
    if crop_w_px > max_w_px or crop_h_px > max_h_px:
        crop_w_px, crop_h_px = max_w_px, max_h_px
        notes.append("too_tight")
    frame_w, _frame_h = full_frame_size(
        aspect, source_width, source_height
    )
    if crop_w_px > 0 and frame_w / crop_w_px > 1.5:
        notes.append("may_look_soft")
    notes.append("size_corrected")
    face = new_profile["face"]
    face_cx_px = float(face["center_x"]) * source_width
    face_cy_px = float(face["center_y"]) * source_height
    predicted = window.get("predicted") or {}
    ref_face = (
        ref_profile.get("face")
        if ref_profile and ref_profile.get("found")
        else {}
    )
    want_cx = float(predicted.get("center_x", ref_face.get("center_x", 0.5)))
    want_cy = float(predicted.get("center_y", ref_face.get("center_y", 0.5)))
    x_px = face_cx_px - want_cx * crop_w_px
    y_px = face_cy_px - want_cy * crop_h_px
    x_px = min(max(x_px, 0.0), source_width - crop_w_px)
    y_px = min(max(y_px, 0.0), source_height - crop_h_px)
    return {
        "x": x_px / source_width,
        "y": y_px / source_height,
        "width": crop_w_px / source_width,
        "height": crop_h_px / source_height,
        "matched": True,
        "notes": notes,
        "predicted": {
            "center_x": (face_cx_px - x_px) / crop_w_px,
            "center_y": (face_cy_px - y_px) / crop_h_px,
            "width": measured_face_w_px / crop_w_px,
        },
    }


def match_report(ref_profile: Optional[dict], output_profile: dict) -> dict:
    """How close an output's face sits to the reference's.

    Compares a profile measured on the rendered output against
    the reference profile it was framed from. ``position_off`` is
    the larger of the x and y center differences, in percent of
    the frame; ``size_off`` is the face width difference as a
    percent of the reference's width share. The plain ``line``
    is the sentence the Finish screen shows; it is only built
    when both profiles hold a face.
    """
    if (
        not ref_profile
        or not ref_profile.get("found")
        or not output_profile
        or not output_profile.get("found")
    ):
        return {"ok": False, "line": ""}
    ref = ref_profile["face"]
    out = output_profile["face"]
    position_off = (
        max(
            abs(float(out["center_x"]) - float(ref["center_x"])),
            abs(float(out["center_y"]) - float(ref["center_y"])),
        )
        * 100
    )
    size_off = (
        abs(float(out["width"]) - float(ref["width"]))
        / float(ref["width"])
        * 100
    )
    return {
        "ok": True,
        "position_off_pct": position_off,
        "size_off_pct": size_off,
        "line": (
            f"Face position within {position_off:.1f} percent, "
            f"face size within {size_off:.1f} percent of the "
            "reference."
        ),
    }


def output_size(crop_w: int, crop_h: int, aspect) -> tuple:
    """The size to scale a crop to: the canonical size for the
    aspect when the crop is bigger, else the crop's own size."""
    target = aspect_value(aspect)
    if target < 1.0:
        canon_w, canon_h = 1080, 1920
    elif target > 1.0:
        canon_w, canon_h = 1920, 1080
    else:
        canon_w, canon_h = 1080, 1080
    if crop_w >= canon_w and crop_h >= canon_h:
        return canon_w, canon_h
    return crop_w, crop_h
