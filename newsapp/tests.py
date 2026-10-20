from django.test import SimpleTestCase, override_settings


@override_settings(SECURE_SSL_REDIRECT=False)
class ServiceStatusTests(SimpleTestCase):
    # SimpleTestCase forbids database queries, including during middleware handling.
    def test_root_and_health_are_public_database_free_status_endpoints(self):
        for path in ("/", "/health/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "ok")
                self.assertEqual(response.json()["links"]["news"], "/api/news/")
                self.assertEqual(response["Cache-Control"], "no-store")

    def test_head_succeeds_without_response_body(self):
        response = self.client.head("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    def test_status_endpoints_reject_writes(self):
        for path in ("/", "/health/"):
            self.assertEqual(self.client.post(path).status_code, 405)

    def test_unknown_routes_still_return_404(self):
        self.assertEqual(self.client.get("/missing-route/").status_code, 404)
