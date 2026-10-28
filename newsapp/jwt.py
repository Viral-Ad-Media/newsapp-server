from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView


class PasswordAwareRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        JWTAuthentication().get_user(RefreshToken(attrs["refresh"]))
        return super().validate(attrs)


class PasswordAwareRefreshView(TokenRefreshView):
    serializer_class = PasswordAwareRefreshSerializer
