from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


class PasswordLoginSecurityTests(TestCase):
    def test_password_endpoints_share_a_five_attempt_budget(self):
        client = APIClient()
        for index in range(5):
            path = "/api/token/" if index % 2 == 0 else "/api/auth/login/"
            response = client.post(
                path,
                {"username": "unknown-login-audit", "password": "incorrect-test-only"},
                format="json",
                REMOTE_ADDR="198.51.100.243",
            )
            self.assertIn(response.status_code, [400, 401])
        for path in ["/api/token/", "/api/auth/login/"]:
            response = client.post(
                path,
                {"username": "unknown-login-audit", "password": "incorrect-test-only"},
                format="json",
                REMOTE_ADDR="198.51.100.243",
            )
            self.assertEqual(response.status_code, 429)

    def test_alternate_password_login_keeps_ordinary_reader_roles(self):
        user = get_user_model().objects.create_user(
            "launch-reader", password="X8!pQ3#tV7@mL2zR5"
        )
        response = APIClient().post(
            "/api/auth/login/",
            {"username": user.username, "password": "X8!pQ3#tV7@mL2zR5"},
            format="json",
            REMOTE_ADDR="198.51.100.244",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
