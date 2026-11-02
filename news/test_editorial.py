from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import (NewsSource, NewsCategory, NewsArticle, ReaderProfile, DevelopingStory,
                     HomepagePlacement, HomepageSection, StoryUpdate, ArticleReport, ReadingHistory)


class EditorialTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user("reader", password="test-reader-password")
        self.staff = get_user_model().objects.create_user("editor", is_staff=True)
        self.source = NewsSource.objects.create(name="Nigeria Desk", url="https://publisher.test", location="ng")
        self.other_source = NewsSource.objects.create(name="Second Desk", url="https://other.test", location="ng")
        self.category = NewsCategory.objects.create(name="Sport")
        self.first = self.article("First report", self.source)
        self.second = self.article("New development", self.other_source)
        self.foreign = self.article("Other edition", self.source, "us")

    def article(self, title, source, location="ng"):
        return NewsArticle.objects.create(title=title, source=source, location=location, description="Source summary", content="Source report", article_url=f"https://publisher.test/{title.replace(' ', '-')}")

    def test_edition_expiry_country_archive_and_curated_priority(self):
        now = timezone.now()
        HomepagePlacement.objects.create(article=self.first, location="ng", slot="lead", ends_at=now+timedelta(hours=1))
        HomepagePlacement.objects.create(article=self.foreign, location="ng", slot="lead", rank=0, ends_at=now+timedelta(hours=1))
        result = self.client.get("/api/edition/?location=ng").json()
        self.assertEqual(result["lead"][0]["id"], self.first.pk)
        self.assertNotIn(self.foreign.pk, [a["id"] for a in result["latest"]])
        HomepagePlacement.objects.filter(article=self.first).update(ends_at=now-timedelta(seconds=1))
        self.assertEqual(self.client.get("/api/edition/?location=ng").json()["lead"][0]["id"], self.second.pk)
        self.second.archived = True
        self.second.save()
        self.assertNotIn(self.second.pk, [a["id"] for a in self.client.get("/api/edition/?location=ng").json()["latest"]])

    def test_editorial_permissions_revision_and_country_validation(self):
        endpoint = "/api/admin/editorial/stories/"
        self.assertEqual(self.client.post(endpoint, {}).status_code, 401)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(endpoint, {}).status_code, 403)
        self.client.force_authenticate(self.staff)
        data = {"title": "Event", "location": "ng", "overview": "Reporting on this event", "published": True, "article_ids": [self.foreign.pk]}
        self.assertEqual(self.client.post(endpoint, data, format="json").status_code, 400)
        data["article_ids"] = [self.first.pk, self.second.pk]
        response = self.client.post(endpoint, data, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        story_id = response.data["id"]
        detail = f"{endpoint}{story_id}/"
        self.assertEqual(self.client.put(detail, {"title": "Overwrite", "revision": 0}, format="json").status_code, 409)
        result = self.client.put(detail, {"title": "Updated event", "revision": 1, "article_ids": [self.second.pk]}, format="json")
        self.assertEqual(result.status_code, 200)
        self.first.refresh_from_db()
        self.assertIsNone(self.first.story_id)

    def test_public_story_hides_archives_and_stale_timeline_evidence(self):
        story = DevelopingStory.objects.create(title="Event", location="ng", published=True)
        self.first.story = story
        self.first.save()
        StoryUpdate.objects.create(story=story, article=self.first, summary="An attributed development")
        StoryUpdate.objects.create(story=story, article=self.second, summary="Wrong event")
        result = self.client.get(f"/api/stories/{story.pk}/").json()
        self.assertEqual(len(result["updates"]), 1)
        self.assertEqual(result["publisher_count"], 1)
        self.assertEqual(self.client.get(f"/api/stories/{story.pk}/?location=us").status_code, 404)
        self.first.archived = True
        self.first.save()
        self.assertEqual(self.client.get(f"/api/stories/{story.pk}/").status_code, 404)

    def test_publisher_count_groups_same_host_and_unknown_bias(self):
        self.other_source.url = "https://www.publisher.test/feed"
        self.other_source.save()
        story = DevelopingStory.objects.create(title="Event", published=True)
        NewsArticle.objects.filter(pk__in=[self.first.pk, self.second.pk]).update(story=story)
        data = self.client.get(f"/api/stories/{story.pk}/").json()
        self.assertEqual(data["report_count"], 2)
        self.assertEqual(data["publisher_count"], 1)
        self.assertEqual(data["articles"][0]["source"]["bias"], "Unrated")

    def test_follow_sources_mute_and_partial_preferences_preserve_topics(self):
        self.client.force_authenticate(self.user)
        profile = ReaderProfile.objects.create(user=self.user)
        profile.categories.add(self.category)
        self.client.put(f"/api/reader-choices/sources/{self.source.pk}/", {}, format="json")
        response = self.client.get("/api/feed/?location=ng")
        self.assertEqual([a["id"] for a in response.data["results"]], [self.first.pk])
        self.assertIn("publisher", response.data["results"][0]["recommendation_reason"])
        self.client.put(f"/api/reader-choices/muted_sources/{self.source.pk}/", {}, format="json")
        response = self.client.get("/api/feed/?location=ng")
        self.assertEqual(response.data["results"], [])
        self.assertTrue(profile.categories.filter(pk=self.category.pk).exists())
        self.assertFalse(profile.followed_sources.exists())
        # Another account cannot see these private preferences.
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get("/api/reader-choices/").data["muted_sources"], [])

    def test_history_reports_and_private_cache(self):
        self.client.force_authenticate(self.user)
        endpoint = f"/api/history/{self.first.pk}/"
        self.assertEqual(self.client.put(endpoint, {}, format="json").status_code, 200)
        self.client.put(endpoint, {}, format="json")
        self.assertEqual(ReadingHistory.objects.filter(user=self.user).count(), 1)
        self.assertIn("no-store", self.client.get("/api/history/")["Cache-Control"])
        self.assertEqual(self.client.post(f"/api/article-reports/{self.first.pk}/", {"message": "Please check the reported publication date."}, format="json").status_code, 201)
        self.assertEqual(ArticleReport.objects.count(), 1)
        self.client.delete("/api/history/")
        self.assertEqual(ReadingHistory.objects.count(), 0)

    def test_coverage_is_staff_only_and_contains_actual_counts(self):
        self.assertEqual(self.client.get("/api/admin/coverage-monitor/").status_code, 401)
        self.client.force_authenticate(self.staff)
        self.first.categories.add(self.category)
        data = self.client.get("/api/admin/coverage-monitor/").json()
        row = next(r for r in data["matrix"] if r["country"] == "ng" and r["category"] == self.category.pk)
        self.assertEqual(row["published"], 1)
        self.assertEqual(row["published_24h"], 1)

    def test_source_review_requires_evidence(self):
        self.client.force_authenticate(self.staff)
        response = self.client.put(f"/api/admin/sources/{self.source.pk}/", {"review_status": "reviewed"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_event_rechecks_membership_country_after_article_edits(self):
        story = DevelopingStory.objects.create(title="Event", location="ng", published=True)
        NewsArticle.objects.filter(pk=self.first.pk).update(story=story)
        StoryUpdate.objects.create(story=story, article=self.first, summary="A sourced development")
        NewsArticle.objects.filter(pk=self.first.pk).update(location="us")
        self.assertEqual(self.client.get(f"/api/stories/{story.pk}/").status_code, 404)
        self.assertIsNone(self.client.get(f"/api/news/{self.first.pk}/").data["developing_story"])

    def test_same_ownership_group_is_not_counted_as_independent_publishers(self):
        self.source.publisher_group = self.other_source.publisher_group = "Shared newsroom"
        self.source.save()
        self.other_source.save()
        story = DevelopingStory.objects.create(title="Event", published=True)
        NewsArticle.objects.filter(pk__in=[self.first.pk, self.second.pk]).update(story=story)
        self.assertEqual(self.client.get(f"/api/stories/{story.pk}/").data["publisher_count"], 1)

    def test_corrections_are_public_but_reader_reports_are_private(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post("/api/admin/editorial/corrections/", {"article": self.first.pk, "note": "Clarified the original date."}, format="json")
        self.assertEqual(response.status_code, 201)
        self.client.force_authenticate(None)
        data = self.client.get(f"/api/news/{self.first.pk}/").json()
        self.assertEqual(data["corrections"][0]["note"], "Clarified the original date.")
        self.assertEqual(self.client.get("/api/admin/editorial/reports/").status_code, 401)

    def test_article_context_needs_support_and_does_not_change_original_date(self):
        self.client.force_authenticate(self.staff)
        endpoint = f"/api/admin/articles/{self.first.pk}/"
        data = {"revision": 1, "why_matters": "This affects local services."}
        self.assertEqual(self.client.put(endpoint, data, format="json").status_code, 400)
        published = self.first.published_at
        data["context_source_url"] = "https://publisher.test/evidence"
        self.assertEqual(self.client.put(endpoint, data, format="json").status_code, 200)
        self.first.refresh_from_db()
        self.assertEqual(self.first.published_at, published)
        self.assertIsNotNone(self.first.updated_at)

    def test_unpublished_events_cannot_be_followed_and_mutations_are_private(self):
        story = DevelopingStory.objects.create(title="Draft event", published=False)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.put(f"/api/reader-choices/stories/{story.pk}/", {}, format="json").status_code, 404)
        response = self.client.put(f"/api/reader-choices/sources/{self.source.pk}/", {}, format="json")
        self.assertIn("no-store", response["Cache-Control"])

    def test_disabling_configured_sections_does_not_restore_automatic_sections(self):
        self.first.categories.add(self.category)
        HomepageSection.objects.create(title="Sport", location="ng", category=self.category, enabled=False)
        self.assertEqual(self.client.get("/api/edition/?location=ng").data["sections"], [])

    def test_unmeasured_sentiment_and_bias_are_not_manufactured(self):
        data = self.client.get(f"/api/news/{self.first.pk}/coverage/").data
        self.assertEqual(data["sentiment_stats"], {"positive": 0, "neutral": 0, "negative": 0})
        self.assertEqual(data["sentiment"], "unavailable")
        self.assertIsNone(data["index_value"])
        self.assertFalse(data["coverage_verified"])
        self.assertEqual(data["articles"][0]["bias_rating"], "Unrated")
