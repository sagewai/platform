import tempfile
import unittest
from pathlib import Path

from desk import AlreadyDecided, ApprovalDesk, EvidenceRequired


class ApprovalDeskTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "desk.db"
        self.desk = ApprovalDesk(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pending_requests_are_project_scoped(self):
        project_a = self.desk.create_request(
            project_id="project-a",
            title="Approve supplier quote",
            risk="low",
        )
        self.desk.create_request(
            project_id="project-b",
            title="Approve another quote",
            risk="low",
        )

        self.assertEqual(
            [request.id for request in self.desk.pending(project_id="project-a")],
            [project_a.id],
        )

    def test_low_risk_request_can_be_approved_with_reason(self):
        request = self.desk.create_request(
            project_id="project-a",
            title="Publish weekly report",
            risk="low",
        )

        decided = self.desk.decide(
            project_id="project-a",
            request_id=request.id,
            decision="approved",
            actor="arda",
            reason="Numbers reconciled",
            evidence_ref="   ",
        )

        self.assertEqual(decided.status, "approved")
        self.assertEqual(decided.decided_by, "arda")
        self.assertIsNone(decided.evidence_ref)
        self.assertEqual(self.desk.pending(project_id="project-a"), [])

    def test_high_risk_approval_requires_evidence(self):
        request = self.desk.create_request(
            project_id="project-a",
            title="Send customer refund batch",
            risk="high",
        )

        with self.assertRaises(EvidenceRequired):
            self.desk.decide(
                project_id="project-a",
                request_id=request.id,
                decision="approved",
                actor="arda",
                reason="Looks correct",
            )

        self.assertEqual(self.desk.get("project-a", request.id).status, "pending")

    def test_evidence_allows_high_risk_approval(self):
        request = self.desk.create_request(
            project_id="project-a",
            title="Send customer refund batch",
            risk="high",
        )

        decided = self.desk.decide(
            project_id="project-a",
            request_id=request.id,
            decision="approved",
            actor="arda",
            reason="Refund totals reconciled",
            evidence_ref="report://refund-reconciliation/42",
        )

        self.assertEqual(decided.evidence_ref, "report://refund-reconciliation/42")

    def test_rejection_is_recorded_without_authorizing_the_action(self):
        request = self.desk.create_request(
            project_id="project-a",
            title="Replace production database",
            risk="critical",
        )

        decided = self.desk.decide(
            project_id="project-a",
            request_id=request.id,
            decision="rejected",
            actor="arda",
            reason="No tested rollback path",
        )

        self.assertEqual(decided.status, "rejected")
        self.assertIsNone(decided.evidence_ref)

    def test_decisions_are_immutable(self):
        request = self.desk.create_request(
            project_id="project-a",
            title="Publish weekly report",
            risk="low",
        )
        self.desk.decide(
            project_id="project-a",
            request_id=request.id,
            decision="approved",
            actor="arda",
            reason="Reviewed",
        )

        with self.assertRaises(AlreadyDecided):
            self.desk.decide(
                project_id="project-a",
                request_id=request.id,
                decision="rejected",
                actor="arda",
                reason="Changed my mind",
            )

    def test_state_and_audit_history_survive_a_restart(self):
        request = self.desk.create_request(
            project_id="project-a",
            title="Approve supplier quote",
            risk="medium",
        )
        self.desk.decide(
            project_id="project-a",
            request_id=request.id,
            decision="approved",
            actor="arda",
            reason="Quote matches the contract",
        )

        restarted = ApprovalDesk(self.database)
        loaded = restarted.get("project-a", request.id)
        history = restarted.history(project_id="project-a", request_id=request.id)

        self.assertEqual(loaded.status, "approved")
        self.assertEqual([event["kind"] for event in history], ["created", "approved"])

    def test_foreign_project_cannot_read_or_decide_request(self):
        request = self.desk.create_request(
            project_id="project-a",
            title="Approve supplier quote",
            risk="low",
        )

        with self.assertRaises(KeyError):
            self.desk.get("project-b", request.id)
        with self.assertRaises(KeyError):
            self.desk.decide(
                project_id="project-b",
                request_id=request.id,
                decision="approved",
                actor="mallory",
                reason="Not my project",
            )


if __name__ == "__main__":
    unittest.main()
