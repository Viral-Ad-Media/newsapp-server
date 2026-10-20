from django.http import JsonResponse
from django.views.decorators.http import require_safe


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
