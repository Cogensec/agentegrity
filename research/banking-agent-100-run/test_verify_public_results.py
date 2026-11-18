"""Pure-stdlib integrity checks for the published, synthetic evaluation table.

No model, bank, network, or external fixture is invoked. Mutations simulate
corrupted public exports and must fail even with Python assertions disabled.
"""
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from verify_public_results import DATA, summarize


class PublicResultsContractTest(unittest.TestCase):
    def test_current_public_outcome_attribution(self) -> None:
        report = summarize()
        self.assertEqual(report["rows"], 300)
        self.assertEqual(report["paired_units"], 100)
        base = report["arms"]["baseline"]
        protected = report["arms"]["protected"]
        allow_all = report["arms"]["protected_approve_all"]
        self.assertEqual(base["unsafe_attack_actions"], 80)
        self.assertEqual(protected["unsafe_attack_actions"], 0)
        self.assertEqual(allow_all["unsafe_attack_actions"], 80)
        self.assertEqual(protected["guard_blocks_on_attack"], 80)
        self.assertEqual(protected["adversarial_flags_on_attack"], 30)
        self.assertEqual(report["attribution"][
            "adversarial_detection_recall_on_scripted_attacks"], 0.375)
        self.assertEqual(report["attribution"][
            "source_of_preventive_denials"], "scenario-coded synthetic approval callback")
        self.assertIs(report["attribution"][
            "external_identity_or_effect_verified"], False)

    def _tamper(self, column: str, value: str, *, arm: str = "protected") -> Path:
        with DATA.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            names = reader.fieldnames
            rows = list(reader)
        self.assertIsNotNone(names)
        row = next(x for x in rows if x["mode"] == arm and x["scenario"] == "S02"
                   and x["iteration"] == "1")
        row[column] = value
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".csv", delete=False
        ) as file:
            writer = csv.DictWriter(file, fieldnames=names)
            writer.writeheader()
            writer.writerows(rows)
            return Path(file.name)

    def test_attack_label_tampering_is_rejected(self) -> None:
        path = self._tamper("attack_present", "False")
        try:
            with self.assertRaisesRegex(ValueError, "attack label changed"):
                summarize(path)
        finally:
            path.unlink()

    def test_approval_ablation_tampering_is_rejected(self) -> None:
        path = self._tamper("unsafe_action", "False", arm="protected_approve_all")
        try:
            with self.assertRaisesRegex(ValueError, "approve-all ablation"):
                summarize(path)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
