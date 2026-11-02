from django.http import JsonResponse
from django.views.decorators.http import require_safe
from dj_rest_auth.views import LoginView
from rest_framework.throttling import ScopedRateThrottle


class PasswordLoginView(LoginView):
    """Use the same budget as /api/token/ for the alternate password entrypoint."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


@require_safe
def service_status(request):
    """Liveness only: do not depend on database or upstream availability."""
    response = JsonResponse(
        {
            "service": "newsapp-server",
            "status": "ok",
            "links": {"news": "/api/news/", "health": "/health/"},
        }
    )
    response["Cache-Control"] = "no-store"
    return response
