from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase

import vercel_build


class VercelBuildTests(SimpleTestCase):
    @patch.dict("os.environ", {"VERCEL_ENV": "preview"}, clear=True)
    @patch("vercel_build.django.setup")
    @patch("vercel_build.call_command")
    def test_preview_does_not_access_database(self, command, setup):
        vercel_build.main()
        command.assert_not_called()
        setup.assert_not_called()

    @patch.dict("os.environ", {"VERCEL_ENV": "production"}, clear=True)
    def test_production_requires_database_url(self):
        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
            vercel_build.main()

    @patch.dict(
        "os.environ",
        {
            "VERCEL_ENV": "production",
            "DATABASE_URL": "postgresql://pooled/db",
            "DATABASE_URL_UNPOOLED": "postgresql://direct/db",
        },
        clear=True,
    )
    @patch("vercel_build.django.setup")
    @patch("vercel_build.connection")
    @patch("vercel_build.call_command")
    def test_success_imports_editorial_batch_after_existing_news(
        self, command, connection, setup
    ):
        connection.vendor = "postgresql"
        cursor = MagicMock()
        cursor.fetchone.return_value = (True,)
        connection.cursor.return_value.__enter__.return_value = cursor
        vercel_build.main()
        self.assertEqual(
            command.call_args_list,
            [
                call("migrate", interactive=False),
                call("seed_news"),
                call("seed_blog_posts"),
            ],
        )
        cursor.execute.assert_any_call(
            "SELECT pg_advisory_unlock(%s)", [vercel_build.DEPLOY_LOCK]
        )
        connection.close.assert_called_once()

    @patch.dict(
        "os.environ",
        {
            "VERCEL_ENV": "production",
            "DATABASE_URL": "postgresql://pooled/db",
            "DATABASE_URL_UNPOOLED": "postgresql://direct/db",
        },
        clear=True,
    )
    @patch("vercel_build.django.setup")
    @patch("vercel_build.connection")
    @patch("vercel_build.call_command")
    def test_migrates_before_seed_and_releases_lock_on_failure(
        self, command, connection, setup
    ):
        connection.vendor = "postgresql"
        cursor = MagicMock()
        cursor.fetchone.return_value = (True,)
        connection.cursor.return_value.__enter__.return_value = cursor
        command.side_effect = [None, RuntimeError("seed failed")]
        with self.assertRaisesRegex(RuntimeError, "seed failed"):
            vercel_build.main()
        self.assertEqual(
            command.call_args_list,
            [call("migrate", interactive=False), call("seed_news")],
        )
        cursor.execute.assert_any_call(
            "SELECT pg_advisory_unlock(%s)", [vercel_build.DEPLOY_LOCK]
        )
        connection.close.assert_called_once()

    @patch.dict(
        "os.environ",
        {
            "VERCEL_ENV": "production",
            "DATABASE_URL": "postgresql://pooled/db",
            "DATABASE_URL_UNPOOLED": "postgresql://direct/db",
        },
        clear=True,
    )
    @patch("vercel_build.django.setup")
    @patch("vercel_build.connection")
    @patch("vercel_build.call_command")
    def test_conflicting_deployment_does_not_modify_database(
        self, command, connection, setup
    ):
        connection.vendor = "postgresql"
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
            False,
        )
        with self.assertRaisesRegex(RuntimeError, "Another database deployment"):
            vercel_build.main()
        command.assert_not_called()
