import os
import subprocess
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase
import render_deploy


ENV = {"SECRET_KEY": "test-render-key", "DATABASE_URL": "postgresql://u:p@db-pooler.example/db",
       "DATABASE_URL_UNPOOLED": "postgresql://u:p@db.example/db"}


class RenderDeployTests(SimpleTestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_credentials_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
            render_deploy.validate_environment()

    @patch.dict(os.environ, {**ENV, "DATABASE_URL_UNPOOLED": ENV["DATABASE_URL"]}, clear=True)
    def test_migrations_reject_pooler(self):
        with self.assertRaisesRegex(RuntimeError, "direct Neon"):
            render_deploy.validate_environment(direct=True)

    @patch.dict(os.environ, ENV, clear=True)
    @patch("django.setup")
    @patch("django.db.connection")
    @patch("django.core.management.call_command")
    def test_release_migrates_without_seeding_or_resetting_users(self, command, connection, setup):
        connection.vendor = "postgresql"
        cursor = MagicMock()
        cursor.fetchone.return_value = (True,)
        connection.cursor.return_value.__enter__.return_value = cursor
        render_deploy.migrate_database()
        self.assertEqual(os.environ["DATABASE_URL"], ENV["DATABASE_URL_UNPOOLED"])
        self.assertEqual(command.call_args_list, [call("migrate", interactive=False)])
        cursor.execute.assert_any_call("SELECT pg_advisory_unlock(%s)", [726491082])
        connection.close.assert_called_once()

    @patch.dict(os.environ, ENV, clear=True)
    @patch("django.setup")
    @patch("django.db.connection")
    @patch("django.core.management.call_command", side_effect=RuntimeError("migration failed"))
    def test_failure_releases_deploy_lock(self, command, connection, setup):
        connection.vendor = "postgresql"
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (True,)
        with self.assertRaisesRegex(RuntimeError, "migration failed"):
            render_deploy.migrate_database()
        connection.cursor.return_value.__enter__.return_value.execute.assert_any_call(
            "SELECT pg_advisory_unlock(%s)", [726491082])
        connection.close.assert_called_once()

    @patch.dict(os.environ, ENV, clear=True)
    @patch("django.setup")
    @patch("django.db.connection")
    @patch("django.core.management.call_command")
    def test_conflicting_deploy_does_not_migrate(self, command, connection, setup):
        connection.vendor = "postgresql"
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (False,)
        with self.assertRaisesRegex(RuntimeError, "Another database deployment"):
            render_deploy.migrate_database()
        command.assert_not_called()
        connection.close.assert_called_once()

    @patch.dict(os.environ, ENV, clear=True)
    @patch("render_deploy.os.execvp")
    @patch("render_deploy.subprocess.run", side_effect=subprocess.CalledProcessError(1, "release"))
    def test_failed_migrations_do_not_start_gunicorn(self, run, execute):
        with self.assertRaises(subprocess.CalledProcessError):
            render_deploy.main("serve")
        execute.assert_not_called()

    @patch.dict(os.environ, {**ENV, "PORT": "12345"}, clear=True)
    @patch("render_deploy.os.execvp")
    @patch("render_deploy.subprocess.run")
    def test_server_keeps_pooled_url_and_binds_platform_port(self, run, execute):
        render_deploy.main("serve")
        self.assertEqual(os.environ["DATABASE_URL"], ENV["DATABASE_URL"])
        self.assertIn("0.0.0.0:12345", execute.call_args.args[1])

    @patch.dict(os.environ, ENV, clear=True)
    @patch("django.setup")
    @patch("django.db.connection")
    @patch("django.db.migrations.executor.MigrationExecutor")
    @patch("render_deploy.os.execvp")
    def test_worker_refuses_unapplied_migrations(self, execute, executor, connection, setup):
        executor.return_value.migration_plan.return_value = [object()]
        with self.assertRaisesRegex(RuntimeError, "web service migrations"):
            render_deploy.main("worker")
        execute.assert_not_called()

    @patch.dict(os.environ, ENV, clear=True)
    @patch("render_deploy.subprocess.run")
    def test_build_only_checks_and_collects_static(self, run):
        render_deploy.main("build")
        self.assertEqual([c.args[0][2] for c in run.call_args_list], ["check", "collectstatic"])
