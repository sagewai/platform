import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


APP = Path(__file__).with_name("app.py")


class ApprovalDeskCliTest(unittest.TestCase):
    def test_create_decide_and_history_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "desk.db"

            created = self.run_app(
                database,
                "create",
                "--project",
                "project-a",
                "--title",
                "Approve supplier quote",
                "--risk",
                "medium",
            )
            request_id = created["id"]

            pending = self.run_app(database, "pending", "--project", "project-a")
            self.assertEqual([item["id"] for item in pending], [request_id])

            decided = self.run_app(
                database,
                "decide",
                request_id,
                "--project",
                "project-a",
                "--decision",
                "approved",
                "--actor",
                "arda",
                "--reason",
                "Quote matches contract",
            )
            self.assertEqual(decided["status"], "approved")

            history = self.run_app(
                database,
                "history",
                request_id,
                "--project",
                "project-a",
            )
            self.assertEqual(
                [event["kind"] for event in history], ["created", "approved"]
            )

    def test_high_risk_cli_approval_without_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "desk.db"
            created = self.run_app(
                database,
                "create",
                "--project",
                "project-a",
                "--title",
                "Send refund batch",
                "--risk",
                "high",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(APP),
                    "--db",
                    str(database),
                    "decide",
                    created["id"],
                    "--project",
                    "project-a",
                    "--decision",
                    "approved",
                    "--actor",
                    "arda",
                    "--reason",
                    "Looks correct",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("requires evidence", completed.stderr)
            pending = self.run_app(database, "pending", "--project", "project-a")
            self.assertEqual([item["id"] for item in pending], [created["id"]])

    def run_app(self, database, *arguments):
        completed = subprocess.run(
            [sys.executable, str(APP), "--db", str(database), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)


if __name__ == "__main__":
    unittest.main()
