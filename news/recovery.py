"""Single-use Django password reset tokens with a configured, trusted frontend URL."""

import logging
from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMessage, get_connection
from django.db import transaction
from django.db.models import Q
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class PasswordResetRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        class Input(serializers.Serializer):
            identifier = serializers.CharField(max_length=254)

        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        origin = settings.PASSWORD_RESET_FRONTEND_URL.rstrip("/")
        parsed = urlsplit(origin)
        backend = settings.EMAIL_BACKEND
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or (
                not settings.DEBUG
                and (
                    parsed.scheme != "https"
                    or backend.endswith(
                        (
                            "console.EmailBackend",
                            "dummy.EmailBackend",
                            "locmem.EmailBackend",
                        )
                    )
                )
            )
            or (backend.endswith("smtp.EmailBackend") and not settings.EMAIL_HOST)
        ):
            return Response(
                {"detail": "Email recovery is not configured. Please contact support."},
                status=503,
            )
        identifier = data.validated_data["identifier"]
        users = (
            get_user_model()
            .objects.filter(
                Q(username__iexact=identifier) | Q(email__iexact=identifier),
                is_active=True,
            )
            .exclude(email="")
        )
        try:
            # Check transport for unknown accounts too; outage responses do not reveal account existence.
            with get_connection(timeout=10) as connection:
                messages = []
                for user in users[:5]:
                    if not user.has_usable_password():
                        continue
                    uid = urlsafe_base64_encode(force_bytes(user.pk))
                    token = default_token_generator.make_token(user)
                    link = f"{origin}/reset-password/{uid}/{token}"
                    minutes = settings.PASSWORD_RESET_TIMEOUT // 60
                    body = f"Reset your AbokiNews password\n\nOpen this secure link to choose a new password:\n{link}\n\nThis link expires in {minutes} minutes and works only once.\nIf you did not request this, ignore this email. Your password has not changed.\n"
                    messages.append(
                        EmailMessage(
                            "Reset your AbokiNews password",
                            body,
                            settings.DEFAULT_FROM_EMAIL,
                            [user.email],
                        )
                    )
                if messages and connection.send_messages(messages) != len(messages):
                    raise RuntimeError("Incomplete email delivery")
        except Exception:
            logger.error("Password reset email transport failed")
            return Response(
                {
                    "detail": "Email recovery is temporarily unavailable. Please try again later."
                },
                status=503,
            )
        response = Response(
            {
                "detail": "If an eligible account matches, a password reset link will be emailed to its registered address."
            }
        )
        response["Cache-Control"] = "no-store"
        return response


class PasswordResetConfirmView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_confirm"

    def post(self, request):
        class Input(serializers.Serializer):
            uid = serializers.CharField(max_length=100)
            token = serializers.CharField(max_length=128)
            password1 = serializers.CharField(max_length=4096, trim_whitespace=False)
            password2 = serializers.CharField(max_length=4096, trim_whitespace=False)

        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        try:
            pk = urlsafe_base64_decode(values["uid"]).decode()
            if not pk.isdigit() or len(pk) > 18:
                raise ValueError()
        except (ValueError, UnicodeError, OverflowError):
            return Response(
                {
                    "detail": "This reset link is invalid or expired. Request a new link."
                },
                status=400,
            )
        with transaction.atomic():
            user = (
                get_user_model()
                .objects.select_for_update()
                .filter(pk=pk, is_active=True)
                .first()
            )
            if (
                not user
                or not user.has_usable_password()
                or not default_token_generator.check_token(user, values["token"])
            ):
                return Response(
                    {
                        "detail": "This reset link is invalid or expired. Request a new link."
                    },
                    status=400,
                )
            if values["password1"] != values["password2"]:
                raise serializers.ValidationError(
                    {"password2": "Passwords do not match."}
                )
            try:
                password_validation.validate_password(values["password1"], user)
            except Exception as exc:
                from django.core.exceptions import ValidationError

                if not isinstance(exc, ValidationError):
                    raise
                raise serializers.ValidationError({"password1": exc.messages})
            user.set_password(values["password1"])
            user.save(update_fields=["password"])
        response = Response(
            {"detail": "Your password has been reset. Sign in with your new password."}
        )
        response["Cache-Control"] = "no-store"
        return response
