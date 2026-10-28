import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .article_writing import (
    article_quality,
    prepare_article,
    ArticlePreparationError,
    source_evidence,
    SourceHTML,
)
from .article_jobs import queue_article, tick_article
from .article_test_data import FULL_ARTICLE, PREPARED
from .models import NewsArticle, NewsSource, ArticlePreparation, AdminEvent
from .automation import store_item


def response(value):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))]
    )


class WritingTests(SimpleTestCase):
    def test_quality_rejects_briefs_roundups_repetition_and_long_paragraphs(self):
        self.assertTrue(article_quality(FULL_ARTICLE)["passed"])
        for body in [
            "A headline and summary.",
            "\n\n".join(f"[Read story {i}](https://example.org/{i})" for i in range(5)),
            FULL_ARTICLE + "\n\n" + FULL_ARTICLE,
            FULL_ARTICLE.replace("\n\n", " "),
        ]:
            self.assertFalse(article_quality(body)["passed"])

    def test_structured_article_is_rendered_as_readable_markdown(self):
        from .article_writing import render_draft_content

        blocks = FULL_ARTICLE.split("\n\n")
        body = render_draft_content(
            {
                "content": {
                    "lead": blocks[0],
                    "sections": [
                        {"heading": blocks[1].lstrip("# "), "paragraphs": blocks[2:4]},
                        {"heading": blocks[4].lstrip("# "), "paragraphs": blocks[5:7]},
                    ],
                    "closing": blocks[7],
                }
            }
        )
        self.assertEqual(body, FULL_ARTICLE)
        self.assertTrue(article_quality(body)["passed"])

    def test_supporting_reference_validation_rejects_credentials_and_other_publishers(
        self,
    ):
        from .article_writing import validate_references

        self.assertEqual(
            validate_references(
                ["https://www.publisher.test/report"], "https://publisher.test/story"
            ),
            ["https://www.publisher.test/report"],
        )
        for url in [
            "https://evil.invalid/report",
            "http://publisher.test/report",
            "https://user:secret@publisher.test/report",
            "https://publisher.test/report?token=secret",
            "https://publisher.test:invalid/report",
        ]:
            with self.assertRaises(ArticlePreparationError):
                validate_references([url], "https://publisher.test/story")

    def test_body_after_heading_needs_no_extra_blank_line(self):
        compact = FULL_ARTICLE.replace(
            "## Solar power and household decisions\n\n",
            "## Solar power and household decisions\n",
        ).replace(
            "## What the example establishes\n\n", "## What the example establishes\n"
        )
        self.assertEqual(
            article_quality(compact)["word_count"],
            article_quality(FULL_ARTICLE)["word_count"],
        )
        self.assertTrue(article_quality(compact)["passed"])

    def test_wordpress_body_and_lists_exclude_unrelated_widgets(self):
        parser = SourceHTML()
        parser.feed(
            '<div role="main"><p>Navigation snippet</p><div class="post-content"><p>Actual reporting.</p><h4>Useful context</h4><ul><li>A factual list item.</li><li><p>Nested paragraph.</p></li></ul><div class="read-also-container"><ul><li>Unrelated headline</li></ul></div><p>Closing evidence.</p></div><p>Footer snippet</p></div>'
        )
        self.assertEqual(
            parser.blocks,
            [
                "Actual reporting.",
                "Useful context",
                "A factual list item.",
                "Nested paragraph.",
                "Closing evidence.",
            ],
        )

    def test_shared_mission_name_is_not_treated_as_copied_reporting(self):
        from .article_writing import copied_prose

        name = "The Crew Health and Performance Exploration Analog Mission Two research team at NASA"
        self.assertEqual(
            copied_prose(
                FULL_ARTICLE + "\n\n" + name, "Different source prose. " + name
            ),
            "",
        )
        self.assertTrue(copied_prose(FULL_ARTICLE, FULL_ARTICLE))

    @patch("news.utils.ai_summarizer.client")
    def test_keyword_placement_is_an_advisory_warning_not_a_factual_rejection(
        self, client
    ):
        result = self.invoke(
            client,
            content=FULL_ARTICLE.replace(
                "## Solar power and household decisions", "## Household decisions"
            ),
        )
        self.assertTrue(result["quality"]["passed"])
        self.assertIn("heading", result["seo"]["warnings"])

    def test_numeric_comparison_handles_separators_and_technical_notation(self):
        from .article_writing import evidence_numbers

        source = evidence_numbers(
            "A 1,700 square foot 3D-printed habitat, 378 days, 45 minutes."
        )
        draft = evidence_numbers(
            "The 1700 square foot 3-D habitat lasted 378 days with a 45-minute delay."
        )
        self.assertEqual(source, draft)
        self.assertNotIn("987654", source)
        self.assertEqual(evidence_numbers("June 19,2025"), {"19", "2025"})

    def test_html_extraction_excludes_navigation_scripts_and_ads(self):
        parser = SourceHTML()
        parser.feed(
            "<nav><p>Navigation</p></nav><main><article><h2>Headline</h2><p>Actual <strong>story</strong>.</p><aside><p>Advert</p></aside><script>bad()</script></article></main><footer><p>Footer</p></footer>"
        )
        self.assertEqual(parser.blocks, ["Headline", "Actual story."])

    @patch("news.safe_fetch.fetch_document")
    def test_source_access_checks_saved_host_robots_and_substance(self, fetch):
        with self.assertRaises(ArticlePreparationError):
            source_evidence("https://other.test/article", "https://publisher.test")
        fetch.assert_not_called()
        fetch.return_value = "User-agent: *\nDisallow: /"
        with self.assertRaises(ArticlePreparationError):
            source_evidence("https://publisher.test/article", "https://publisher.test")
        self.assertEqual(fetch.call_count, 1)
        fetch.side_effect = ["", "<article><p>Just a teaser</p></article>"]
        with self.assertRaises(ArticlePreparationError):
            source_evidence("https://publisher.test/article", "https://publisher.test")

    @patch("news.safe_fetch.fetch_document")
    def test_publisher_subdomain_is_allowed_but_lookalike_domain_is_not(self, fetch):
        with self.assertRaises(ArticlePreparationError):
            source_evidence(
                "https://publisher.test.attacker.invalid/article",
                "https://publisher.test",
            )
        fetch.assert_not_called()
        fetch.side_effect = [
            "",
            "<article><p>" + FULL_ARTICLE.replace("\n\n", "</p><p>") + "</p></article>",
        ]
        result = source_evidence(
            "https://science.publisher.test/article", "https://www.publisher.test"
        )
        self.assertIn("Solar power", result)
        self.assertEqual(fetch.call_count, 2)

    @patch("news.safe_fetch.fetch_document")
    def test_redirect_target_is_checked_against_publisher_robots_policy(self, fetch):
        def document(url, **kwargs):
            if url.endswith("/robots.txt"):
                return "User-agent: *\nDisallow: /private"
            kwargs["url_check"]("https://publisher.test/private")
            return "<article><p>Should never be read.</p></article>"

        fetch.side_effect = document
        with self.assertRaisesRegex(ArticlePreparationError, "robots policy"):
            source_evidence("https://publisher.test/story", "https://publisher.test")
        self.assertEqual(fetch.call_count, 2)

    @patch("news.utils.ai_summarizer.client", None)
    def test_missing_credential_never_returns_placeholder_article(self):
        with self.assertRaisesRegex(ArticlePreparationError, "OPENAI_API_KEY"):
            prepare_article(
                title="Solar power",
                summary="Keep summary",
                evidence=FULL_ARTICLE,
                source_name="Test",
                source_url="https://example.org/story",
            )

    @patch("news.utils.ai_summarizer.client")
    def test_source_credentials_are_rejected_before_provider_call(self, client):
        for url in [
            "https://user:private@publisher.test/story",
            "https://publisher.test/story?api_key=private",
            "https://publisher.test/story?access_token=private",
        ]:
            with self.assertRaises(ArticlePreparationError):
                prepare_article(
                    title="Source story",
                    summary="Keep summary",
                    evidence=FULL_ARTICLE,
                    source_name="Publisher",
                    source_url=url,
                )
        client.with_options.assert_not_called()

    def invoke(self, client, content=FULL_ARTICLE, verdict=None):
        draft = {
            key: PREPARED[key]
            for key in ["focus_keyword", "seo_title", "seo_description"]
        }
        draft["content"] = content
        client.with_options.return_value.chat.completions.create.side_effect = [
            response(draft),
            response(verdict or {"supported": True, "issues": []}),
        ]
        # Disjoint test evidence isolates the model-response contract. Source accuracy is not claimed by this mocked test.
        return prepare_article(
            title="Solar power guidance",
            summary="Unchanged briefing.",
            evidence=(
                "Verified testing evidence about a fictional community energy information programme. "
                * 55
            ),
            source_name="Fixture",
            source_url="https://example.org/story",
        )

    @patch("news.utils.ai_summarizer.client")
    def test_structured_response_passes_quality_and_existing_seo_checks(self, client):
        result = self.invoke(client)
        self.assertTrue(result["quality"]["passed"])
        self.assertGreaterEqual(result["seo"]["score"], 80)
        self.assertEqual(result["content"].count("https://"), 1)
        self.assertNotIn("description", result)
        self.assertEqual(client.with_options.call_count, 2)

    @patch("news.utils.ai_summarizer.client")
    def test_one_correction_pass_revalidates_before_source_review(self, client):
        valid = {
            key: PREPARED[key]
            for key in ["focus_keyword", "seo_title", "seo_description"]
        }
        valid["content"] = FULL_ARTICLE
        invalid = {**valid, "seo_description": "Too short"}
        client.with_options.return_value.chat.completions.create.side_effect = [
            response(invalid),
            response(valid),
            response({"supported": True, "issues": []}),
        ]
        result = prepare_article(
            title="Solar power guidance",
            summary="Unchanged briefing.",
            evidence=(
                "Verified testing evidence about a fictional community energy information programme. "
                * 55
            ),
            source_name="Fixture",
            source_url="https://example.org/story",
        )
        self.assertTrue(result["quality"]["passed"])
        self.assertEqual(client.with_options.call_count, 3)

    @patch("news.utils.ai_summarizer.client")
    def test_supporting_sources_are_cited_without_changing_summary(self, client):
        valid = {
            key: PREPARED[key]
            for key in ["focus_keyword", "seo_title", "seo_description"]
        }
        valid["content"] = FULL_ARTICLE
        client.with_options.return_value.chat.completions.create.side_effect = [
            response(valid),
            response({"supported": True, "issues": []}),
        ]
        result = prepare_article(
            title="Solar power guidance",
            summary="Unchanged summary.",
            evidence=(
                "Verified testing evidence about a fictional community energy information programme. "
                * 55
            ),
            source_name="Fixture",
            source_url="https://example.org/story",
            references=["https://example.org/guide"],
        )
        self.assertIn(
            "[Supporting source 1](https://example.org/guide)", result["content"]
        )
        self.assertTrue(article_quality(result["content"])["passed"])
        self.assertEqual(result["content"].count("https://"), 2)
        self.assertNotIn("description", result)
        self.assertEqual(
            client.with_options.return_value.chat.completions.create.call_args_list[
                0
            ].kwargs["response_format"]["type"],
            "json_schema",
        )

    @patch("news.utils.ai_summarizer.client")
    def test_explicit_insufficient_evidence_is_not_expanded_with_filler(self, client):
        client.with_options.return_value.chat.completions.create.return_value = (
            response(
                {
                    "insufficient_evidence": True,
                    "content": {"lead": "", "sections": [], "closing": ""},
                    "focus_keyword": "",
                    "seo_title": "",
                    "seo_description": "",
                }
            )
        )
        with self.assertRaisesRegex(ArticlePreparationError, "cannot support"):
            prepare_article(
                title="Solar power guidance",
                summary="Keep summary",
                evidence=FULL_ARTICLE,
                source_name="Fixture",
                source_url="https://example.org/story",
            )
        self.assertEqual(client.with_options.call_count, 1)

    @patch("news.utils.ai_summarizer.client")
    def test_unsupported_claim_review_blocks_writing(self, client):
        with self.assertRaisesRegex(ArticlePreparationError, "source-support"):
            self.invoke(
                client,
                verdict={"supported": False, "issues": ["Unsupported causal claim"]},
            )

    @patch("news.utils.ai_summarizer.client")
    def test_unverified_numbers_rejected_before_second_model_call(self, client):
        with self.assertRaisesRegex(ArticlePreparationError, "numbers"):
            self.invoke(
                client, FULL_ARTICLE + "\n\nThe programme received 987654 dollars."
            )
        self.assertEqual(client.with_options.call_count, 2)

    @patch("news.utils.ai_summarizer.client")
    def test_verbatim_passages_are_rejected(self, client):
        draft = {**PREPARED, "content": FULL_ARTICLE}
        client.with_options.return_value.chat.completions.create.return_value = (
            response(draft)
        )
        with self.assertRaisesRegex(ArticlePreparationError, "repeats source"):
            prepare_article(
                title="Solar power",
                summary="Keep",
                evidence=FULL_ARTICLE,
                source_name="Fixture",
                source_url="https://example.org/story",
            )


class PreparationJobTests(TestCase):
    def setUp(self):
        self.source = NewsSource.objects.create(
            name="Publisher", url="https://example.org"
        )
        self.article = NewsArticle.objects.create(
            title="Solar power guidance",
            description="Do not change this summary.",
            content="Legacy short body.",
            author="Original author",
            source=self.source,
            article_url="https://example.org/story",
            published_at=timezone.now() - timezone.timedelta(days=5),
        )
        self.client = APIClient()
        self.staff = get_user_model().objects.create_user(
            username="writer", is_staff=True
        )

    def test_queue_permission_idempotency_and_private_backups(self):
        url = f"/api/admin/articles/{self.article.pk}/prepare/"
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 401)
        self.client.force_authenticate(self.staff)
        first = self.client.post(url, {}, format="json")
        self.assertEqual(first.status_code, 202)
        self.client.post(url, {}, format="json")
        self.assertEqual(ArticlePreparation.objects.count(), 1)
        self.assertEqual(self.client.get("/api/admin/preparations/").status_code, 200)
        public = self.client.get(f"/api/news/{self.article.pk}/").data
        self.assertNotIn("original", public)
        self.assertNotIn("evidence", public)

    def test_supporting_references_require_verified_text_and_are_private(self):
        self.client.force_authenticate(self.staff)
        url = f"/api/admin/articles/{self.article.pk}/prepare/"
        self.assertEqual(
            self.client.post(
                url, {"references": ["https://example.org/guide"]}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(ArticlePreparation.objects.count(), 0)
        response = self.client.post(
            url,
            {"evidence": FULL_ARTICLE, "references": ["https://example.org/guide"]},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        job = ArticlePreparation.objects.get()
        self.assertEqual(job.result["input_references"], ["https://example.org/guide"])
        self.assertNotIn(
            "input_references", self.client.get(f"/api/news/{self.article.pk}/").data
        )

    @patch("news.utils.ai_summarizer.client", None)
    def test_blocked_job_preserves_existing_article_and_has_reason(self):
        job = queue_article(self.article.pk)
        completed = tick_article()
        self.assertEqual(completed.pk, job.pk)
        self.assertEqual(completed.status, "blocked")
        self.assertIn("OPENAI_API_KEY", completed.error)
        self.article.refresh_from_db()
        self.assertEqual(self.article.content, "Legacy short body.")
        self.assertEqual(self.article.description, "Do not change this summary.")
        self.assertEqual(self.article.revision, 1)

    @patch("news.utils.ai_summarizer.client", object())
    @patch("news.article_jobs.prepare_article", return_value=PREPARED)
    def test_upgrade_retains_summary_date_author_url_and_previous_body(self, prepare):
        old_date = self.article.published_at
        job = queue_article(self.article.pk, FULL_ARTICLE)
        self.assertEqual(tick_article().status, "completed")
        self.article.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(self.article.content, PREPARED["content"])
        self.assertEqual(self.article.description, "Do not change this summary.")
        self.assertEqual(self.article.published_at, old_date)
        self.assertEqual(self.article.author, "Original author")
        self.assertEqual(self.article.article_url, "https://example.org/story")
        self.assertEqual(job.original["content"], "Legacy short body.")
        self.assertEqual(job.evidence, "")
        self.assertEqual(self.article.revision, 2)
        self.assertIsNone(tick_article())

    @patch("news.utils.ai_summarizer.client", object())
    @patch("news.article_jobs.prepare_article", return_value=PREPARED)
    def test_custom_seo_metadata_is_preserved(self, prepare):
        self.article.seo_title = "Deliberate editorial search title"
        self.article.save()
        queue_article(self.article.pk, FULL_ARTICLE)
        tick_article()
        self.article.refresh_from_db()
        self.assertEqual(self.article.seo_title, "Deliberate editorial search title")

    @patch("news.utils.ai_summarizer.client", object())
    @patch("news.article_jobs.prepare_article", return_value=PREPARED)
    def test_post_save_failure_rolls_back_body_replacement(self, prepare):
        queue_article(self.article.pk, FULL_ARTICLE)
        with patch("news.article_jobs.event", side_effect=RuntimeError("failure")):
            self.assertEqual(tick_article().status, "blocked")
        self.article.refresh_from_db()
        self.assertEqual(self.article.content, "Legacy short body.")
        self.assertEqual(self.article.revision, 1)

    def test_run_generation_budget_defers_without_model_or_publication(self):
        item = {
            "title": "Deferred story",
            "description": "Summary",
            "article_url": "https://example.org/deferred",
            "published_at": timezone.now(),
        }
        with patch("news.article_writing.prepare_article") as prepare:
            self.assertEqual(store_item(item, self.source, budget=[0]), "deferred")
            prepare.assert_not_called()
        self.assertEqual(NewsArticle.objects.count(), 1)

    def test_concurrent_editor_change_cancels_stale_upgrade(self):
        queue_article(self.article.pk)
        self.article.content = "New editor text"
        self.article.save()
        self.assertEqual(tick_article().status, "blocked")
        self.article.refresh_from_db()
        self.assertEqual(self.article.content, "New editor text")

    @patch("news.utils.ai_summarizer.client", None)
    def test_new_rss_item_is_not_published_when_writer_unavailable(self):
        item = {
            "title": "New story",
            "description": "Keep summary.",
            "content": FULL_ARTICLE,
            "article_url": "https://example.org/new",
            "published_at": timezone.now(),
        }
        with self.assertRaises(ArticlePreparationError):
            store_item(item, self.source)
        self.assertEqual(NewsArticle.objects.count(), 1)
        self.assertEqual(store_item(item, self.source, dry_run=True), "eligible")
        self.assertEqual(NewsArticle.objects.count(), 1)

    def test_manual_publish_rejects_brief_and_accepts_full_story(self):
        self.client.force_authenticate(self.staff)
        payload = {
            "title": "Editor report",
            "description": "Preserved summary.",
            "source": self.source.pk,
            "content": "Brief",
        }
        self.assertEqual(
            self.client.post(
                "/api/admin/articles/", payload, format="json"
            ).status_code,
            400,
        )
        payload["content"] = FULL_ARTICLE
        published = self.client.post("/api/admin/articles/", payload, format="json")
        self.assertEqual(published.status_code, 201)
        self.assertEqual(published.data["description"], payload["description"])
        self.assertTrue(published.data["quality"]["passed"])


class ArticleRedirectTests(SimpleTestCase):
    @patch(
        "news.safe_fetch.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    @patch("news.safe_fetch.urllib3.HTTPSConnectionPool")
    def test_same_publisher_redirect_revalidates_dns_and_certificate(
        self, factory, dns
    ):
        from news.safe_fetch import fetch_document

        first, second = MagicMock(), MagicMock()
        factory.side_effect = [first, second]
        first.urlopen.return_value.status = 301
        first.urlopen.return_value.headers = {
            "Location": "https://www.publisher.test/new"
        }
        second.urlopen.return_value.status = 200
        second.urlopen.return_value.headers = {}
        second.urlopen.return_value.read.side_effect = [b"<article>OK</article>", b""]
        self.assertEqual(
            fetch_document(
                "https://publisher.test/old", redirects=2, allowed_host="publisher.test"
            ),
            "<article>OK</article>",
        )
        self.assertEqual(dns.call_count, 2)
        self.assertEqual(
            factory.call_args_list[1].kwargs["assert_hostname"], "www.publisher.test"
        )
        self.assertFalse(second.urlopen.call_args.kwargs["redirect"])

    @patch(
        "news.safe_fetch.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    @patch("news.safe_fetch.urllib3.HTTPSConnectionPool")
    def test_off_publisher_redirect_is_rejected_before_dns_or_connection(
        self, factory, dns
    ):
        from news.safe_fetch import fetch_document

        factory.return_value.urlopen.return_value.status = 302
        factory.return_value.urlopen.return_value.headers = {
            "Location": "https://publisher.test.evil.invalid/article"
        }
        with self.assertRaisesRegex(ValueError, "publisher domain"):
            fetch_document(
                "https://publisher.test/old", redirects=2, allowed_host="publisher.test"
            )
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(factory.call_count, 1)

    @patch(
        "news.safe_fetch.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    @patch("news.safe_fetch.urllib3.HTTPSConnectionPool")
    def test_source_redirect_cannot_downgrade_to_http_or_loop_forever(
        self, factory, dns
    ):
        from news.safe_fetch import fetch_document

        factory.return_value.urlopen.return_value.status = 302
        factory.return_value.urlopen.return_value.headers = {
            "Location": "http://publisher.test/article"
        }
        with self.assertRaises(ValueError):
            fetch_document(
                "https://publisher.test/old", redirects=2, allowed_host="publisher.test"
            )
        factory.return_value.urlopen.return_value.headers = {"Location": "/old"}
        with self.assertRaises(ValueError):
            fetch_document(
                "https://publisher.test/old", redirects=2, allowed_host="publisher.test"
            )
        self.assertLessEqual(factory.call_count, 4)

    @patch("news.safe_fetch.socket.getaddrinfo")
    @patch("news.safe_fetch.urllib3.HTTPSConnectionPool")
    def test_redirect_to_publisher_host_with_private_dns_is_blocked(self, factory, dns):
        from news.safe_fetch import fetch_document

        dns.side_effect = [
            [(2, 1, 6, "", ("93.184.216.34", 443))],
            [(2, 1, 6, "", ("127.0.0.1", 443))],
        ]
        factory.return_value.urlopen.return_value.status = 302
        factory.return_value.urlopen.return_value.headers = {
            "Location": "https://science.publisher.test/article"
        }
        with self.assertRaises(ValueError):
            fetch_document(
                "https://publisher.test/story",
                redirects=2,
                allowed_host="publisher.test",
            )
        self.assertEqual(factory.call_count, 1)
