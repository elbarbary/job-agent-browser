from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docx import Document

from app.config import Settings
from app.cv_store import ingest_cv


class CVStoreTests(unittest.TestCase):
    def test_docx_ingestion_preserves_known_facts_and_marks_unknowns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings.load(root)
            cv = root / "resume.docx"
            document = Document()
            for line in (
                "Ada Example",
                "ada@example.test",
                "Skills",
                "Python, Linux, Terraform",
                "Education",
                "BSc Computer Science",
            ):
                document.add_paragraph(line)
            document.save(cv)
            result = ingest_cv(cv, settings)
            self.assertEqual(result.profile["email"], "ada@example.test")
            self.assertIn("Python, Linux, Terraform", result.profile["skills"])
            questions = result.profile["constraints_questions_needing_user_confirmation"]
            self.assertTrue(any("work authorization" in question for question in questions))
            self.assertEqual(result.profile_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
