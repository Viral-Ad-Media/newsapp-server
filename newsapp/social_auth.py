"""Google identity verification and conversion to the reader's JWT session."""

from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from django.conf import settings
from dj_rest_auth.registration.serializers import SocialLoginSerializer
from dj_rest_auth.registration.views import SocialLoginView
from rest_framework import serializers, status
from rest_framework.response import Response


class GoogleLoginSerializer(SocialLoginSerializer):
    def validate(self, attrs):
        # Require the signed identity token so allauth verifies issuer, expiry,
        # signature and audience against our Google client, not just userinfo.
        if not attrs.get("access_token") or not attrs.get("id_token"):
            raise serializers.ValidationError("Google access and identity tokens are required.")
        return super().validate(attrs)


class GoogleLoginView(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    serializer_class = GoogleLoginSerializer

    def get_response(self):
        response = super().get_response()
        # Next.js owns the browser's HttpOnly cookies. Return the refresh token
        # to its server callback, just as /api/token/ does for password login.
        if response.status_code == 200:
            response.data["refresh"] = str(self.refresh_token)
        return response

    def post(self, request, *args, **kwargs):
        if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
            return Response(
                {"detail": "Google sign-in is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return super().post(request, *args, **kwargs)
