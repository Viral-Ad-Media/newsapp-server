import re
from datetime import timedelta
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .models import NewsArticle, NewsSource, NewsCategory
from .utils.ai_summarizer import AIUnavailable, answer_general_question


class ReaderUpdatesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.source = NewsSource.objects.create(
            name="Publisher", url="https://example.org"
        )
        self.now = timezone.now()
        self.recent = self.article(
            "Football championship update", self.now - timedelta(hours=2)
        )
        self.old = self.article("Older report", self.now - timedelta(days=40))
        self.user = get_user_model().objects.create_user(
            username="reset-reader",
            email="reader@example.org",
            password="Old-test-only-password!",
        )

    def article(self, title, date):
        return NewsArticle.objects.create(
            title=title,
            description="Stored source summary.",
            content="Summary.",
            source=self.source,
            published_at=date,
            location="ng",
        )

    def test_time_windows_are_strict_not_anchored_to_old_articles(self):
        self.article("Week report", self.now - timedelta(days=4))
        self.article("Month report", self.now - timedelta(days=20))
        self.article("Future report", self.now + timedelta(hours=1))
        for window, count in [("last_day", 1), ("last_week", 2), ("last_month", 3)]:
            res = self.client.get("/api/news/", {"filter": window, "location": "ng"})
            self.assertEqual(res.data["count"], count)
        self.recent.delete()
        self.assertEqual(
            self.client.get("/api/news/", {"filter": "last_day"}).data["count"], 0
        )

    def test_news_answer_is_only_real_links_and_never_calls_ai(self):
        with patch("news.utils.ai_summarizer.answer_general_question") as ai:
            res = self.client.post(
                "/api/ask/",
                {"question": "Latest football news", "location": "ng"},
                format="json",
            )
        self.assertEqual(res.data["kind"], "news")
        self.assertEqual(res.data["answer"], "")
        self.assertEqual([s["id"] for s in res.data["sources"]], [self.recent.pk])
        ai.assert_not_called()

    def test_specific_news_does_not_fall_back_to_unrelated_articles(self):
        res = self.client.post(
            "/api/ask/",
            {"question": "Latest volcano news", "location": "ng"},
            format="json",
        )
        self.assertEqual(res.data["sources"], [])
        self.assertEqual(res.data["kind"], "news")

    def test_ai_news_window_country_and_archive_constraints(self):
        foreign = self.article("Foreign report", self.now - timedelta(hours=1))
        foreign.location = "us"
        foreign.save()
        hidden = self.article("Hidden report", self.now - timedelta(hours=1))
        hidden.archived = True
        hidden.save()
        res = self.client.post(
            "/api/ask/",
            {"question": "Latest headlines in the last 24 hours", "location": "ng"},
            format="json",
        )
        self.assertEqual([s["id"] for s in res.data["sources"]], [self.recent.pk])

    def test_general_question_returns_only_text(self):
        with patch(
            "news.utils.ai_summarizer.answer_general_question",
            return_value="Photosynthesis converts light into chemical energy.",
        ) as ai:
            res = self.client.post(
                "/api/ask/",
                {"question": "Explain photosynthesis", "location": "ng"},
                format="json",
            )
        self.assertEqual(res.data["kind"], "general")
        self.assertEqual(res.data["sources"], [])
        self.assertIn("Photosynthesis", res.data["answer"])
        ai.assert_called_once()

    def test_general_ai_unavailable_is_honest(self):
        with patch(
            "news.utils.ai_summarizer.answer_general_question",
            side_effect=AIUnavailable,
        ):
            res = self.client.post(
                "/api/ask/", {"question": "What is photosynthesis?"}, format="json"
            )
        self.assertEqual(res.status_code, 503)
        self.assertNotIn("sources", res.data)

    def test_general_model_timeout_failure_and_prompt(self):
        client = MagicMock()
        client.with_options.return_value.chat.completions.create.return_value.choices[
            0
        ].message.content = "A useful answer."
        with patch("news.utils.ai_summarizer.client", client):
            self.assertEqual(
                answer_general_question("Explain fractions"), "A useful answer."
            )
            self.assertEqual(client.with_options.call_args.kwargs["timeout"], 12)
            args = (
                client.with_options.return_value.chat.completions.create.call_args.kwargs
            )
            self.assertIn("no browsing tools", args["messages"][0]["content"])
            client.with_options.side_effect = RuntimeError("secret-provider-key")
            with self.assertRaises(AIUnavailable):
                answer_general_question("Hello")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        PASSWORD_RESET_FRONTEND_URL="https://reader.example",
        DEBUG=True,
        ALLOWED_HOSTS=["testserver", "evil.example"],
    )
    def test_reset_email_confirm_single_use_and_session_revocation(self):
        tokens = self.client.post(
            "/api/token/",
            {"username": self.user.username, "password": "Old-test-only-password!"},
            format="json",
        ).data
        res = self.client.post(
            "/api/password-reset/",
            {"identifier": self.user.email},
            format="json",
            HTTP_HOST="evil.example",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(
            r"https://reader.example/reset-password/([^/]+)/([^\s]+)",
            mail.outbox[0].body,
        )
        self.assertIsNotNone(link)
        uid, token = link.groups()
        payload = dict(
            uid=uid,
            token=token,
            password1="New-test-only-password-42!",
            password2="New-test-only-password-42!",
        )
        self.assertEqual(
            self.client.post(
                "/api/password-reset/confirm/", payload, format="json"
            ).status_code,
            200,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(payload["password1"]))
        self.assertEqual(
            self.client.post(
                "/api/password-reset/confirm/", payload, format="json"
            ).status_code,
            400,
        )
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])
        self.assertEqual(self.client.get("/api/auth/user/").status_code, 401)
        self.client.credentials()
        self.assertEqual(
            self.client.post(
                "/api/token/refresh/", {"refresh": tokens["refresh"]}, format="json"
            ).status_code,
            401,
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        PASSWORD_RESET_FRONTEND_URL="https://reader.example",
        DEBUG=True,
    )
    def test_unknown_or_inactive_account_gets_same_generic_reply(self):
        unknown = self.client.post(
            "/api/password-reset/", {"identifier": "unknown@example.org"}, format="json"
        )
        known = self.client.post(
            "/api/password-reset/", {"identifier": self.user.email}, format="json"
        )
        self.assertEqual(unknown.data, known.data)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="",
        DEBUG=False,
    )
    def test_unconfigured_production_email_does_not_claim_success(self):
        res = self.client.post(
            "/api/password-reset/", {"identifier": self.user.email}, format="json"
        )
        self.assertEqual(res.status_code, 503)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEBUG=True
    )
    def test_transport_error_sanitized(self):
        with patch(
            "news.recovery.get_connection",
            side_effect=RuntimeError("secret-smtp-password"),
        ):
            for identifier in [self.user.email, "unknown@example.org"]:
                res = self.client.post(
                    "/api/password-reset/", {"identifier": identifier}, format="json"
                )
                self.assertEqual(res.status_code, 503)
                self.assertNotIn("secret", str(res.data))

    def test_invalid_expired_and_weak_reset_password_rejected(self):
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes

        token = default_token_generator.make_token(self.user)
        payload = dict(
            uid=urlsafe_base64_encode(force_bytes(self.user.pk)),
            token=token,
            password1="12345678",
            password2="12345678",
        )
        self.assertEqual(
            self.client.post(
                "/api/password-reset/confirm/", payload, format="json"
            ).status_code,
            400,
        )
        payload.update(password1="Strong-test-only-42!", password2="different")
        self.assertEqual(
            self.client.post(
                "/api/password-reset/confirm/", payload, format="json"
            ).status_code,
            400,
        )
        payload.update(password2=payload["password1"])
        with patch(
            "django.contrib.auth.tokens.PasswordResetTokenGenerator._now",
            return_value=timezone.now().replace(tzinfo=None) + timedelta(hours=2),
        ):
            self.assertEqual(
                self.client.post(
                    "/api/password-reset/confirm/", payload, format="json"
                ).status_code,
                400,
            )

class PublicationVisibilityTests(TestCase):
    @patch('news.utils.ai_summarizer.client', new=object())
    @patch('news.article_writing.prepare_article')
    def test_imported_old_report_is_new_in_latest_live_and_24_hours(self, prepare):
        from .automation import store_item
        from .test_article_writing import PREPARED
        prepare.return_value = PREPARED
        source = NewsSource.objects.create(name='Publisher',url='https://example.org',location='ng')
        original = timezone.now()-timedelta(days=7)
        item = {'title':'Newly published football report','description':'A real source report','article_url':'https://example.org/new-report','published_at':original,'content':' '.join(['Evidence']*450),'categories':['Sports']}
        store_item(item,source,False)
        article=NewsArticle.objects.get(title=item['title'])
        self.assertEqual(article.source_published_at, original)
        self.assertGreater(article.published_at, timezone.now()-timedelta(minutes=1))
        client=APIClient();user=get_user_model().objects.create_user('live-reader');client.force_authenticate(user)
        for path in ['/api/news/?location=ng','/api/news/?location=ng&filter=last_day','/api/live/?location=ng']:
            result=client.get(path)
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.data['results'][0]['id'],article.pk)
            self.assertIn('no-store',result['Cache-Control'])
        edition=client.get('/api/edition/?location=ng')
        self.assertEqual(edition.data['latest'][0]['id'],article.pk)

    def test_existing_import_date_repair_preserves_original_and_manual_dates(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        from types import SimpleNamespace
        source=NewsSource.objects.create(name='Date publisher',url='https://example.org')
        original=timezone.now()-timedelta(days=10)
        imported=NewsArticle.all_objects.create(title='Existing import',description='Report',content='Report',source=source,imported=True,published_at=original)
        manual=NewsArticle.all_objects.create(title='Manual article',description='Report',content='Report',source=source,published_at=original)
        migration=importlib.import_module('news.migrations.0027_newsarticle_source_published_at')
        for _ in range(2):migration.separate_publication_dates(apps,SimpleNamespace(connection=connection))
        imported.refresh_from_db();manual.refresh_from_db()
        self.assertEqual(imported.source_published_at,original)
        self.assertEqual(imported.published_at,imported.created_at)
        self.assertEqual(manual.published_at,original)
