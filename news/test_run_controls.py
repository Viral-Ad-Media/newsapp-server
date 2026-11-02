from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .automation import config, queue_run
from .models import (
    CrawlRun,
    AdminEvent,
    NewsArticle,
    NewsSource,
    SourcePublishingWindow,
)


class DashboardRunControlTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = get_user_model().objects.create_user("run-operator", is_staff=True)
        self.reader = get_user_model().objects.create_user("run-reader")
        self.client.force_authenticate(self.staff)

    def test_each_button_queues_its_correct_mode_and_dashboard_reports_it(self):
        for dry_run in (False, True):
            response = self.client.post(
                "/api/admin/runs/", {"dry_run": dry_run}, format="json"
            )
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.data["dry_run"], dry_run)
            dashboard = self.client.get("/api/admin/dashboard/").data
            self.assertEqual(dashboard["active_run"]["id"], response.data["id"])
            self.assertFalse(dashboard["scheduler_healthy"])
            self.assertTrue(dashboard['can_cancel_pending_runs'])
            self.assertEqual(
                self.client.post(
                    f"/api/admin/runs/{response.data['id']}/cancel/", {}, format="json"
                ).status_code,
                200,
            )
        self.assertEqual(NewsArticle.all_objects.count(), 0)

    def test_pending_conflict_is_informative_and_does_not_create_duplicates(self):
        run = queue_run(dry_run=True)
        response = self.client.post(
            "/api/admin/runs/", {"dry_run": False}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn(f"Run #{run.pk}", response.data["detail"])
        self.assertIn("Cancel pending run", response.data["detail"])
        self.assertEqual(CrawlRun.objects.count(), 1)
        run.refresh_from_db()
        self.assertTrue(run.dry_run)

    def test_cancellation_recovers_waiting_job_and_preserves_history(self):
        source = NewsSource.objects.create(
            name="Stored publisher", url="https://example.org"
        )
        article = NewsArticle.objects.create(
            source=source,
            title="Already published",
            description="Stored summary",
            content="Stored body",
        )
        window = SourcePublishingWindow.objects.create(source=source, counts={"*": 1})
        run = queue_run()
        response = self.client.post(
            f"/api/admin/runs/{run.pk}/cancel/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, "cancelled")
        self.assertIsNotNone(run.completed_at)
        self.assertTrue(
            AdminEvent.objects.filter(
                run=run, actor=self.staff, status="cancelled"
            ).exists()
        )
        self.assertIsNone(self.client.get("/api/admin/dashboard/").data["active_run"])
        next_run = self.client.post(
            "/api/admin/runs/", {"dry_run": True}, format="json"
        )
        self.assertEqual(next_run.status_code, 202)
        self.assertTrue(next_run.data["dry_run"])
        self.assertTrue(CrawlRun.objects.filter(pk=run.pk).exists())
        self.assertTrue(NewsArticle.objects.filter(pk=article.pk).exists())
        window.refresh_from_db()
        self.assertEqual(window.counts, {"*": 1})

    def test_executing_worker_fence_prevents_cancellation(self):
        run = queue_run()
        with patch(
            "news.automation.AutomationWorkerLock.objects.select_for_update",
            side_effect=DatabaseError("lock unavailable"),
        ):
            response = self.client.post(
                f"/api/admin/runs/{run.pk}/cancel/", {}, format="json"
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("executing a batch", response.data["detail"])
        run.refresh_from_db()
        self.assertEqual(run.status, "queued")

    def test_cancel_is_staff_only_post_only_and_idempotent(self):
        run = queue_run()
        path = f"/api/admin/runs/{run.pk}/cancel/"
        self.assertEqual(self.client.get(path).status_code, 404)
        for user, expected in [(None, 401), (self.reader, 403)]:
            self.client.force_authenticate(user)
            self.assertEqual(
                self.client.post(path, {}, format="json").status_code, expected
            )
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(path, {}, format="json").status_code, 200)
        self.assertEqual(self.client.post(path, {}, format="json").status_code, 200)

    def test_completed_job_cannot_be_cancelled_and_heartbeat_is_reported(self):
        run = CrawlRun.objects.create(trigger="manual", status="success")
        response = self.client.post(
            f"/api/admin/runs/{run.pk}/cancel/", {}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        cfg = config()
        cfg.worker_seen = timezone.now()
        cfg.save()
        self.assertTrue(
            self.client.get("/api/admin/dashboard/").data["scheduler_healthy"]
        )
        run.refresh_from_db()
        self.assertEqual(run.status, "success")
