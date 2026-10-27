"""Command line interface for Talk to Your Editor.

Commands:
  validate-operations  Strictly validate an operations JSON file.
  map-time             Map a finished-edit timestamp to source and word.
  direct               Feedback JSON -> operations -> verified revised plan.
  apply                Apply an operations JSON file to a cuts plan.
  inspect              Summarize a transcript + cuts job.

Integration hooks for later commands (after-review, learning) can register
additional subcommands in ``build_parser`` without touching these handlers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .applier import ApplyError, apply_verdicts
from .meta_provider import MetaProvider, MockProvider, ProviderError
from .schema import SchemaError, validate_operations
from .timeline import Timeline
from .verifier import (
    plain_applied_action,
    plain_held_action,
    verify_operations,
)


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_optional(path: Optional[str]) -> Optional[dict]:
    return _load_json(path) if path else None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def _print(data) -> None:
    print(json.dumps(data, indent=2))


def _timeline(args) -> Timeline:
    return Timeline(_load_json(args.transcript), _load_json(args.cuts))


def cmd_validate_operations(args) -> int:
    try:
        ops = validate_operations(_load_json(args.operations))
    except SchemaError as exc:
        _print({"valid": False, "reasons": exc.reasons})
        return 2
    result = {"valid": True, "operations": [op.to_dict() for op in ops]}
    if getattr(args, "cuts", None):
        timeline = _timeline(args)
        verdicts = verify_operations(ops, timeline, _load_optional(args.profile))
        result["verdicts"] = [v.to_dict() for v in verdicts]
    _print(result)
    return 0


def cmd_map_time(args) -> int:
    timeline = _timeline(args)
    try:
        mapping = timeline.map_output_time(args.time)
    except ValueError as exc:
        _print({"error": str(exc)})
        return 2
    _print(mapping)
    return 0


def cmd_inspect(args) -> int:
    timeline = _timeline(args)
    profile = _load_optional(getattr(args, "profile", None))
    kept = timeline.kept_word_indices()
    _print(
        {
            "source_duration_s": timeline.source_duration,
            "output_duration_s": round(timeline.output_duration, 4),
            "fps": timeline.fps,
            "kept_ranges": len(timeline.ranges),
            "special_ranges": sum(
                1 for s in timeline.segments if s["special"]
            ),
            "joins": len(timeline.joins()),
            "words_total": len(timeline.words),
            "words_kept": len(kept),
            "words_cut": len(timeline.words) - len(kept),
            "protected_spans": timeline.protected_spans(profile),
        }
    )
    return 0


_STATUS_FOR_PAGE = {"apply": "applied", "flag": "flagged",
                    "reject": "rejected"}


def _validate_for_apply(ops_data) -> list:
    """Validate operations for the verify-and-apply path.

    Each operation is validated on its own, so two operations whose
    edits overlap are not a batch schema failure here: the verifier
    resolves overlaps deterministically (the first edit that checks
    out applies, the conflicting one is flagged for the creator), and
    the run always completes. Any other schema problem still raises
    SchemaError with every reason collected. The standalone
    ``validate_operations`` keeps its strict batch behaviour for
    validating a submitted operations file.
    """
    if isinstance(ops_data, dict):
        if "operations" not in ops_data:
            raise SchemaError(["missing_field:operations"])
        items = ops_data["operations"]
    else:
        items = ops_data
    if not isinstance(items, list):
        raise SchemaError(["invalid_type:operations"])
    ops: list = []
    reasons: list[str] = []
    for item in items:
        try:
            ops.extend(validate_operations([item]))
        except SchemaError as exc:
            reasons.extend(exc.reasons)
    if reasons:
        raise SchemaError(reasons)
    return ops


def _verify_and_apply(args, ops_data, out_dir: Path) -> dict:
    ops = _validate_for_apply(ops_data)
    timeline = _timeline(args)
    profile = _load_optional(getattr(args, "profile", None))
    verdicts = verify_operations(ops, timeline, profile)
    cuts = _load_json(args.cuts)
    revised, audit = apply_verdicts(cuts, timeline, verdicts)
    # A flat per-operation view of the verdicts, in the shape the
    # pages consume: one entry per operation, carrying its outcome
    # and a plain_action written for a person. Every entry carries
    # one: an applied change says what it did, and a held or rejected
    # change says why in plain sentences mapped from the verdict
    # reasons. The raw reason codes stay in the audit's verdicts;
    # they never appear in this text.
    page_operations = []
    for verdict in verdicts:
        entry = dict(verdict.operation.to_dict())
        entry["status"] = _STATUS_FOR_PAGE.get(
            verdict.status, verdict.status)
        if verdict.status == "apply":
            entry["plain_action"] = plain_applied_action(
                verdict.operation.type
            )
        else:
            entry["plain_action"] = plain_held_action(verdict.reasons)
        page_operations.append(entry)
    audit["operations"] = page_operations
    _write_json(out_dir / "revised_cuts.json", revised)
    _write_json(out_dir / "operations_audit.json", audit)
    return audit


def cmd_apply(args) -> int:
    out_dir = Path(args.out_dir)
    try:
        audit = _verify_and_apply(args, _load_json(args.operations), out_dir)
    except SchemaError as exc:
        _print({"valid": False, "reasons": exc.reasons})
        return 2
    except ApplyError as exc:
        _print({"applied": False, "error": str(exc)})
        return 2
    _print(
        {
            "applied_comment_ids": audit["applied_comment_ids"],
            "flagged_comment_ids": audit["flagged_comment_ids"],
            "rejected_comment_ids": audit["rejected_comment_ids"],
            "before": audit["before"],
            "after": audit["after"],
            "out_dir": str(out_dir),
        }
    )
    return 0


def cmd_direct(args) -> int:
    timeline = _timeline(args)
    profile = _load_optional(getattr(args, "profile", None))
    feedback = _load_json(args.feedback)
    if args.provider == "meta":
        provider = MetaProvider(allow_wonder=not args.no_wonder)
    else:
        provider = MockProvider()
    try:
        operations, routing = provider.direct(feedback, timeline, profile)
    except ProviderError as exc:
        _print({"provider_error": str(exc)})
        return 2
    out_dir = Path(args.out_dir)
    _write_json(out_dir / "operations.json", {"operations": operations})
    try:
        audit = _verify_and_apply(
            args, {"operations": operations}, out_dir
        )
    except SchemaError as exc:
        _print({"valid": False, "reasons": exc.reasons})
        return 2
    except ApplyError as exc:
        _print({"applied": False, "error": str(exc)})
        return 2
    audit_with_routing = dict(audit)
    audit_with_routing["routing"] = routing
    _write_json(out_dir / "operations_audit.json", audit_with_routing)
    _print(
        {
            "provider": args.provider,
            "operations": operations,
            "applied_comment_ids": audit["applied_comment_ids"],
            "flagged_comment_ids": audit["flagged_comment_ids"],
            "rejected_comment_ids": audit["rejected_comment_ids"],
            "before": audit["before"],
            "after": audit["after"],
            "out_dir": str(out_dir),
        }
    )
    return 0


def cmd_after_review(args) -> int:
    from .after_review import generate_after_review

    out = generate_after_review(
        args.feedback,
        args.operations,
        args.out,
        video_filename=args.video,
    )
    _print({"after_review": str(out)})
    return 0


def cmd_serve(args) -> int:
    from .webapp import serve

    return serve(
        args.job_dir,
        host=args.host,
        port=args.port,
        render_py=args.render_py,
        source_video=args.source,
        cleaned_wav=args.cleaned_wav,
    )


def cmd_learn(args) -> int:
    from .learning import (
        build_template_draft,
        infer_template_from_pairs,
        load_graded_records,
        write_template_draft,
    )

    if args.pairs:
        payload = _load_json(args.pairs)
        summaries = payload if isinstance(payload, list) else (
            payload.get("entries") or payload.get("records") or [])
        draft = infer_template_from_pairs(summaries)
    elif args.graded:
        records = load_graded_records(args.graded)
        draft = build_template_draft(
            records, creator=args.creator, min_support=args.min_support)
    else:
        _print({"error": "provide --graded or --pairs"})
        return 2
    out = write_template_draft(
        draft, args.out, active_template_path=args.active_template)
    _print(
        {
            "template_draft": str(out),
            "candidate_rules": len(draft.get("candidate_rules", [])),
            "all_candidates_unapproved": all(
                not c.get("approved", True)
                for c in draft.get("candidate_rules", [])),
            "active_template_modified": False,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tye", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-operations")
    p.add_argument("--operations", required=True)
    p.add_argument("--transcript")
    p.add_argument("--cuts")
    p.add_argument("--profile")
    p.set_defaults(func=cmd_validate_operations)

    p = sub.add_parser("map-time")
    p.add_argument("--transcript", required=True)
    p.add_argument("--cuts", required=True)
    p.add_argument("--time", required=True, type=float)
    p.set_defaults(func=cmd_map_time)

    p = sub.add_parser("inspect")
    p.add_argument("--transcript", required=True)
    p.add_argument("--cuts", required=True)
    p.add_argument("--profile")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("apply")
    p.add_argument("--operations", required=True)
    p.add_argument("--transcript", required=True)
    p.add_argument("--cuts", required=True)
    p.add_argument("--profile")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("direct")
    p.add_argument("--feedback", required=True)
    p.add_argument("--transcript", required=True)
    p.add_argument("--cuts", required=True)
    p.add_argument("--profile")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--provider", choices=["mock", "meta"], default="mock")
    p.add_argument("--no-wonder", action="store_true")
    p.set_defaults(func=cmd_direct)

    p = sub.add_parser("after-review")
    p.add_argument("--feedback", required=True)
    p.add_argument("--operations", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--video")
    p.set_defaults(func=cmd_after_review)

    p = sub.add_parser("serve")
    p.add_argument("--job-dir", required=True)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--render-py")
    p.add_argument("--source")
    p.add_argument("--cleaned-wav")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("learn")
    p.add_argument("--graded")
    p.add_argument("--pairs")
    p.add_argument("--out", required=True)
    p.add_argument("--active-template")
    p.add_argument("--creator", default="Sample Creator")
    p.add_argument("--min-support", type=int, default=2)
    p.set_defaults(func=cmd_learn)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: file not found: {exc.filename}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
