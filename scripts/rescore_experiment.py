"""Rescore a persisted real-evidence experiment without deleting history.

Used when a later pilot reveals that an experiment's declared expectations were
too weak. The original correctness scores are preserved inside
``evaluation_json.rescore_history`` and an audit event is recorded; only the
current ``correctness`` columns are corrected so the database no longer reports
a misleading "correct" result.

Example::

    python scripts/rescore_experiment.py \
        --experiment-id experiment_ce80695d0b9549ea98f051b560acb113 \
        --add-must-mention-any "Ethernet1/0,Et1/0,Ethernet 1/0" \
        --reason "pilot 2 showed the fault port must be named" \
        --actor vlan-pilot
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from collector import db
from test_cases.evaluation import aggregate_results, score_answer


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def rescore(
    experiment_id: str,
    *,
    add_must_mention_any: list[str] | None = None,
    replace_must_mention: dict[str, str] | None = None,
    reason: str,
    actor: str = "rescore",
) -> dict:
    experiment = db.get_experiment(experiment_id)
    if not experiment:
        raise SystemExit(f"experiment not found: {experiment_id}")
    original_summary = dict(experiment.get("summary") or {})
    additions = [term for term in (add_must_mention_any or []) if term]
    replacements = {
        old: new for old, new in (replace_must_mention or {}).items() if old and new
    }
    if not additions and not replacements:
        raise SystemExit(
            "provide --add-must-mention-any or --replace-must-mention"
        )

    timestamp = _utcnow()
    summary_rows = []
    changed = []
    case_updates: dict[str, dict] = {}

    for result in experiment["results"]:
        expected = dict(result.get("expected") or {})
        answer = result.get("answer") or {}
        previous = {
            "correctness": result.get("correctness"),
            "correctness_score": result.get("correctness_score"),
            "executability_score": result.get("executability_score"),
        }

        terms = list(expected.get("must_mention_any") or [])
        for term in additions:
            if term not in terms:
                terms.append(term)
        expected["must_mention_any"] = terms

        must_terms = [
            replacements.get(str(term), str(term))
            for term in (expected.get("must_mention") or [])
        ]
        expected["must_mention"] = must_terms
        case_updates[result["case_id"]] = expected

        new_score = score_answer(answer, expected)
        evaluation = dict(result.get("evaluation") or {})
        history = list(evaluation.get("rescore_history") or [])
        history.append(
            {
                "rescored_at_utc": timestamp,
                "actor": actor,
                "reason": reason,
                "added_must_mention_any": additions,
                "replaced_must_mention": replacements,
                "previous": previous,
                "new": {
                    "correctness": new_score["correctness"],
                    "correctness_score": new_score["correctness_score"],
                    "matched_terms": new_score["matched_terms"],
                    "missing_terms": new_score["missing_terms"],
                },
            }
        )
        evaluation.update(new_score)
        evaluation["rescore_history"] = history

        with db.get_conn() as conn:
            conn.execute(
                """UPDATE experiment_results
                   SET correctness=?, correctness_score=?, executability_score=?,
                       evaluation_json=?
                   WHERE result_id=?""",
                (
                    new_score["correctness"],
                    new_score["correctness_score"],
                    new_score["executability_score"],
                    _json(evaluation),
                    result["result_id"],
                ),
            )
        changed.append(
            {
                "diagnosis_id": result.get("diagnosis_id"),
                "kb_enabled": bool(result.get("kb_enabled")),
                "previous": previous,
                "correctness": new_score["correctness"],
                "matched_terms": new_score["matched_terms"],
                "missing_terms": new_score["missing_terms"],
            }
        )
        summary_rows.append(
            {
                "kb_enabled": bool(result.get("kb_enabled")),
                "correctness": new_score["correctness"],
                "executability_score": new_score["executability_score"],
                "total_response_time_ms": result.get("total_response_time_ms"),
            }
        )

    # Keep the declared test-case expectations consistent with the rescore.
    with db.get_conn() as conn:
        for case_id, expected in case_updates.items():
            conn.execute(
                "UPDATE test_cases SET expected_json=? WHERE case_id=?",
                (_json(expected), case_id),
            )

    summary = aggregate_results(summary_rows)
    for key in (
        "same_evidence_all_cases",
        "case_external_id",
        "question",
        "category",
        "evidence_run_id",
    ):
        if key in original_summary:
            summary[key] = original_summary[key]
    summary["rescore"] = {
        "rescored_at_utc": timestamp,
        "actor": actor,
        "reason": reason,
        "added_must_mention_any": additions,
        "original_summary": original_summary,
    }
    db.finish_experiment_run(
        experiment_id, status="completed", summary=summary
    )
    db.record_audit_event(
        actor=actor,
        action="evaluation.experiment.rescored",
        target_type="experiment",
        target_id=experiment_id,
        details={
            "reason": reason,
            "added_must_mention_any": additions,
            "results": changed,
        },
    )
    return {
        "experiment_id": experiment_id,
        "summary": summary,
        "results": changed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument(
        "--add-must-mention-any",
        default="",
        help="Comma-separated terms; the answer must mention at least one.",
    )
    parser.add_argument(
        "--replace-must-mention",
        default="",
        help="Comma-separated old=new term replacements for must_mention.",
    )
    parser.add_argument("--reason", required=True)
    parser.add_argument("--actor", default="rescore")
    args = parser.parse_args()
    replacements = {}
    for pair in args.replace_must_mention.split(","):
        if "=" in pair:
            old, new = pair.split("=", 1)
            if old.strip() and new.strip():
                replacements[old.strip()] = new.strip()
    payload = rescore(
        args.experiment_id,
        add_must_mention_any=[
            term.strip()
            for term in args.add_must_mention_any.split(",")
            if term.strip()
        ],
        replace_must_mention=replacements,
        reason=args.reason,
        actor=args.actor,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
