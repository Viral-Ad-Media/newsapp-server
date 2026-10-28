from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework import serializers
from rest_framework.test import APIRequestFactory
from rest_framework.response import Response

from .social_auth import GoogleLoginSerializer, GoogleLoginView


class GoogleAuthenticationTests(SimpleTestCase):
    @override_settings(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="")
    def test_unconfigured_google_returns_service_unavailable(self):
        request = APIRequestFactory().post("/api/auth/google/", {}, format="json")
        response = GoogleLoginView.as_view()(request)
        self.assertEqual(response.status_code, 503)

    def test_signed_identity_and_access_token_are_both_required(self):
        for payload in ({}, {"access_token": "access"}, {"id_token": "identity"}):
            with self.subTest(payload=payload):
                serializer = GoogleLoginSerializer(data=payload)
                self.assertFalse(serializer.is_valid())

    @patch("dj_rest_auth.registration.serializers.SocialLoginSerializer.validate")
    def test_tokens_are_passed_to_allauth_validation(self, validate):
        validate.side_effect = serializers.ValidationError("Invalid signature")
        serializer = GoogleLoginSerializer(
            data={"access_token": "access", "id_token": "invalid-identity"}
        )
        self.assertFalse(serializer.is_valid())
        validate.assert_called_once()

    @patch("dj_rest_auth.registration.views.SocialLoginView.get_response")
    def test_google_returns_refresh_token_for_frontend_cookie_exchange(self, get_response):
        get_response.return_value = Response({"access": "access", "refresh": ""})
        view = GoogleLoginView()
        view.refresh_token = "reader-refresh"
        response = view.get_response()
        self.assertEqual(response.data["refresh"], "reader-refresh")
