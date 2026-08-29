from __future__ import annotations

import argparse
import json
from pathlib import Path

from desk import ApprovalDesk


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Durable project-scoped approval desk")
    parser.add_argument("--db", type=Path, default=Path("approval-desk.db"))
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create a pending request")
    create.add_argument("--project", required=True)
    create.add_argument("--title", required=True)
    create.add_argument(
        "--risk", choices=("low", "medium", "high", "critical"), required=True
    )

    pending = commands.add_parser("pending", help="List pending requests")
    pending.add_argument("--project", required=True)

    decide = commands.add_parser("decide", help="Approve or reject a request")
    decide.add_argument("request_id")
    decide.add_argument("--project", required=True)
    decide.add_argument("--decision", choices=("approved", "rejected"), required=True)
    decide.add_argument("--actor", required=True)
    decide.add_argument("--reason", required=True)
    decide.add_argument("--evidence-ref")

    history = commands.add_parser("history", help="Read a request audit history")
    history.add_argument("request_id")
    history.add_argument("--project", required=True)
    return parser


def execute(args: argparse.Namespace):
    desk = ApprovalDesk(args.db)
    if args.command == "create":
        return desk.create_request(
            project_id=args.project,
            title=args.title,
            risk=args.risk,
        ).to_dict()
    if args.command == "pending":
        return [request.to_dict() for request in desk.pending(project_id=args.project)]
    if args.command == "decide":
        return desk.decide(
            project_id=args.project,
            request_id=args.request_id,
            decision=args.decision,
            actor=args.actor,
            reason=args.reason,
            evidence_ref=args.evidence_ref,
        ).to_dict()
    return desk.history(project_id=args.project, request_id=args.request_id)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = execute(args)
    except (KeyError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
