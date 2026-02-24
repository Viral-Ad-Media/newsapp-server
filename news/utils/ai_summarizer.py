from decouple import config

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None

OPENAI_API_KEY = config("OPENAI_API_KEY", default="")
OPENAI_SUMMARY_MODEL = config("OPENAI_SUMMARY_MODEL", default="gpt-4o-mini")
client = OpenAI(api_key=OPENAI_API_KEY) if OpenAI and OPENAI_API_KEY else None

def summarize_article(content):
    """
    Summarize a news article using OpenAI's GPT.
    Args:
        content (str): The article's full text content.
    Returns:
        str: A summarized version of the article.
    """
    if not content:
        return ""

    if client is None:
        return "Unable to summarize this article at the moment."

    try:
        response = client.chat.completions.create(
            model=OPENAI_SUMMARY_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You summarize news articles in concise, neutral language.",
                },
                {
                    "role": "user",
                    "content": f"Summarize the following article:\n\n{content}",
                },
            ],
            max_tokens=150,
            temperature=0.3,
        )
        summary = (response.choices[0].message.content or "").strip()
        return summary
    except Exception:
        return "Unable to summarize this article at the moment."
