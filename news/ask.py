"""Grounded news navigation and general Q&A have distinct response contracts."""

import re
from datetime import timedelta
from django.db.models import Q, Case, When, IntegerField, Value
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from .models import NewsArticle
from .regions import filter_country, COUNTRY_ALIASES

STOP = set(
    "headline article story reporting hour hours hrs last day days week weeks month months now current you we list search read display provide explain summarize ".split()
) | set(
    "what when where which who how why show find give tell about latest stored news headlines stories articles reports report happened happening going today recent updates please platform aboki abokinews have does this that with from your there their are for and the can could would should has was were will been much some any me our us".split()
)


class AskNews(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ask"

    def post(self, request):
        class Input(serializers.Serializer):
            question = serializers.CharField(max_length=500)
            location = serializers.CharField(
                max_length=100, required=False, allow_blank=True
            )

        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        question = data.validated_data["question"]
        lower = question.casefold()
        terms = list(
            dict.fromkeys(
                t
                for t in re.findall(r"[^\W_]+", lower, re.UNICODE)
                if len(t) > 2 and t not in STOP
            )
        )[:12]
        location = data.validated_data.get("location", "").lower()
        aliases = COUNTRY_ALIASES.get(location, [])
        terms = [
            term
            for term in terms
            if term not in {word for alias in aliases for word in alias.split()}
        ]
        if re.search(
            r"\b(reset|forgot|change)\b.*\bpassword\b", lower
        ) and not re.search(r"\b(news|headlines?|latest|articles?|report)\b", lower):
            return Response(
                {
                    "kind": "general",
                    "answer": "Open Sign in, choose Forgot password, and enter your account email or username. Open the reset link sent to your email and choose a new password. The link expires after one hour. Never share your password or reset link.",
                    "sources": [],
                }
            )
        explicit = bool(
            re.search(
                r"\b(news|headlines?|articles?|reporting|latest|stor(?:y|ies)|happened|happening|on (this|the) (site|platform)|aboki)\b",
                lower,
            )
        )
        conceptual = bool(
            re.match(
                r"^(what (is|are)|define|explain|how (do|does|to|can)|write|translate|calculate|help me)\b",
                lower,
            )
        )
        if conceptual and not re.search(
            r"\b(latest|headlines?|stor(?:y|ies)|articles?|happening|today|reported|news (about|on|in|from))\b",
            lower,
        ):
            explicit = False
        qs = filter_country(
            NewsArticle.objects.select_related("source"),
            data.validated_data.get("location"),
        )
        query, score = Q(), Value(0, output_field=IntegerField())
        for term in terms:
            query |= (
                Q(title__icontains=term)
                | Q(description__icontains=term)
                | Q(categories__name__iexact=term)
            )
            score += Case(
                When(title__icontains=term, then=Value(4)),
                When(description__icontains=term, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        matching = (
            qs.filter(query).annotate(relevance=score).distinct()
            if terms
            else qs.none()
        )
        news = explicit or (not conceptual and matching.exists())
        if news:
            if re.search(r"\b(24\s*(hours?|hrs?)|today|last day)\b", lower):
                now = timezone.now()
                qs = qs.filter(
                    published_at__gte=now - timedelta(hours=24), published_at__lte=now
                )
                matching = matching.filter(
                    published_at__gte=now - timedelta(hours=24), published_at__lte=now
                )
            # A specific topic never falls back to unrelated latest stories.
            articles = list(
                (
                    matching.order_by("-relevance", "-published_at", "-id")
                    if terms
                    else qs.order_by("-published_at", "-id")
                )[:5]
            )
            response = Response(
                {
                    "kind": "news",
                    "answer": "",
                    "sources": [
                        {
                            "id": a.pk,
                            "title": a.title,
                            "source": a.source.name,
                            "published_at": a.published_at,
                        }
                        for a in articles
                    ],
                    "message": (
                        ""
                        if articles
                        else "No matching published news was found in this country edition. Try another topic or a wider time window."
                    ),
                }
            )
        else:
            from .utils.ai_summarizer import answer_general_question, AIUnavailable

            try:
                answer = answer_general_question(question)
            except AIUnavailable:
                return Response(
                    {
                        "detail": "General AI answers are temporarily unavailable. News search still works. Please try again later."
                    },
                    status=503,
                )
            response = Response({"kind": "general", "answer": answer, "sources": []})
        response["Cache-Control"] = "private, no-store"
        return response
