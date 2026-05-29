from __future__ import annotations

import unittest

from app.job_sources import FeedItem, _looks_relevant, clean_html, make_job_id
from app.source_catalog import all_source_domains
from app.web_search import build_job_search_terms


class JobSourcesTests(unittest.TestCase):
    def test_catalog_feeds_search_terms(self) -> None:
        domains = all_source_domains()
        self.assertIn("jobs.lever.co", domains)
        self.assertIn("myworkdayjobs.com", domains)
        terms = build_job_search_terms("AI product", "Switzerland", ["jobs.lever.co", "jobs.ashbyhq.com"])
        self.assertIn("site:jobs.lever.co", terms)
        self.assertIn("site:jobs.ashbyhq.com", terms)

    def test_clean_html_extracts_text(self) -> None:
        self.assertEqual(clean_html("<p>Hello <strong>AI</strong></p>"), "Hello\nAI")

    def test_job_id_is_stable(self) -> None:
        url = "https://remoteok.com/remote-jobs/example"
        self.assertEqual(make_job_id(url), make_job_id(url))

    def test_feed_item_shape(self) -> None:
        item = FeedItem(
            source="test",
            title="AI Product Engineer",
            company="Example",
            location="Remote",
            url="https://example.com/job",
            description="LLM product work",
        )
        self.assertEqual(item.title, "AI Product Engineer")

    def test_relevance_filter_rejects_noise(self) -> None:
        rejected_titles = (
            "Office Assistant",
            "Junior Creative Strategist",
            "People Business Partner",
            "Product Manager Retail Lending & Enablement",
            "Entry Level Administrative Assistant",
            "Junior Project Manager",
            "Junior Artist Manager / Influencer Manager",
        )
        for title in rejected_titles:
            with self.subTest(title=title):
                self.assertFalse(
                    _looks_relevant(
                        FeedItem(
                            source="test",
                            title=title,
                            company="Example",
                            location="Remote",
                            url="https://example.com/job",
                            description="General office work with AI tools and possible visa sponsorship.",
                        )
                    )
                )
        self.assertTrue(
            _looks_relevant(
                FeedItem(
                    source="test",
                    title="Graduate AI Product Engineer",
                    company="Example",
                    location="Example City",
                    url="https://example.com/job2",
                    description="Visa sponsorship available.",
                )
            )
        )
        self.assertTrue(
            _looks_relevant(
                FeedItem(
                    source="test",
                    title="Associate Product Manager, AI Platform",
                    company="Example",
                    location="Remote",
                    url="https://example.com/job3",
                    description="Technical product work across APIs and ML systems.",
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
