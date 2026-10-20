from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase
from rest_framework.test import APIClient

from news.models import NewsArticle
from news.seo import analyze_seo


class SeoAnalysisTests(SimpleTestCase):
    def test_score_is_explained_and_total_is_one_hundred(self):
        draft = {
            "title": "Solar power: a practical household guide",
            "seo_description": "Solar power explained for households: compare equipment, estimate energy use and ask useful installation questions before choosing a system for your home.",
            "focus_keyword": "solar power",
            "content": "Solar power helps explain this example.\n\n## Solar power choices\n\n"
            + "\n\n".join(
                [
                    "A clear paragraph about equipment and practical household choices. "
                    * 7
                ]
                * 5
            )
            + "\n\n[Related](/news/1) and [Evidence](https://example.org/report)",
            "remote_image_url": "https://example.org/image.jpg",
            "image_alt": "Panels on a roof",
        }
        result = analyze_seo(draft)
        self.assertEqual(sum(c["points"] for c in result["checks"]), 100)
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["internal_links"], 1)
        self.assertEqual(result["external_links"], 1)
        self.assertGreaterEqual(result["word_count"], 300)

    def test_blank_keyword_does_not_match_everything(self):
        result = analyze_seo({"title": "Test", "content": "Test"})
        checks = {c["id"]: c["passed"] for c in result["checks"]}
        self.assertFalse(checks["keyword_title"])
        self.assertFalse(checks["intro"])
        self.assertFalse(checks["heading"])

    def test_phrase_boundaries_and_same_origin_links(self):
        result = analyze_seo(
            {
                "focus_keyword": "art",
                "title": "Earth",
                "content": "[One](https://news.example/news/2)\n\n[Two](https://other.example/a)",
                "site_url": "https://news.example",
            }
        )
        checks = {c["id"]: c["passed"] for c in result["checks"]}
        self.assertFalse(checks["keyword_title"])
        self.assertEqual(result["internal_links"], 1)
        self.assertEqual(result["external_links"], 1)


class SeoApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.editor = get_user_model().objects.create_user(
            username="seo-editor", is_staff=True
        )
        self.payload = {
            "title": "A new editorial article",
            "description": "A useful summary",
            "content": "A useful body",
            "seo_title": "Distinct search title",
            "focus_keyword": "editorial",
            "seo_description": "Custom search description",
            "canonical_url": "https://news.example/news/1",
            "seo_noindex": True,
            "schema_type": "BlogPosting",
            "image_alt": "Actual image description",
        }

    def test_permissions_and_analysis_is_read_only(self):
        self.assertIn(
            self.client.post(
                "/api/superadmin/seo/analyze/", self.payload, format="json"
            ).status_code,
            (401, 403),
        )
        self.client.force_authenticate(self.editor)
        res = self.client.post(
            "/api/superadmin/seo/analyze/", self.payload, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["indexing"], "noindex")
        self.assertFalse(NewsArticle.objects.exists())
        reader = get_user_model().objects.create_user(username="ordinary-reader")
        self.client.force_authenticate(reader)
        self.assertEqual(
            self.client.post(
                "/api/superadmin/seo/analyze/", {}, format="json"
            ).status_code,
            403,
        )

    def test_metadata_survives_create_update_and_public_read(self):
        self.client.force_authenticate(self.editor)
        res = self.client.post("/api/superadmin/articles/", self.payload, format="json")
        self.assertEqual(res.status_code, 201)
        article_id = res.data["id"]
        self.assertEqual(res.data["seo_title"], self.payload["seo_title"])
        self.assertTrue(res.data["seo_noindex"])
        res = self.client.put(
            f"/api/superadmin/articles/{article_id}/",
            {"seo_title": "", "seo_noindex": False, "og_title": "Share this title"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["seo_title"], "")
        self.assertFalse(res.data["seo_noindex"])
        self.client.force_authenticate(None)
        public = self.client.get(f"/api/news/{article_id}/")
        self.assertEqual(public.data["og_title"], "Share this title")
        self.assertEqual(public.data["schema_type"], "BlogPosting")

    def test_invalid_metadata_is_rejected_before_creating_article(self):
        self.client.force_authenticate(self.editor)
        for change in (
            {"canonical_url": "javascript:alert(1)"},
            {"seo_title": "a" * 121},
            {"schema_type": "FakeReview"},
            {"seo_noindex": "maybe"},
        ):
            res = self.client.post(
                "/api/superadmin/articles/", {**self.payload, **change}, format="json"
            )
            self.assertEqual(res.status_code, 400)
        self.assertFalse(NewsArticle.objects.exists())
