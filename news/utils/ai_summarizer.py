import openai
from decouple import config

# Load OpenAI API Key
openai.api_key = config("OPENAI_API_KEY")

def summarize_article(content):
    """
    Summarize a news article using OpenAI's GPT.
    Args:
        content (str): The article's full text content.
    Returns:
        str: A summarized version of the article.
    """
    try:
        response = openai.Completion.create(
            engine="text-davinci-003",  # Or another model, e.g., gpt-4
            prompt=f"Summarize the following article:\n\n{content}",
            max_tokens=150,
            temperature=0.7,
            top_p=1.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )
        summary = response.choices[0].text.strip()
        return summary
    except Exception as e:
        print(f"Error summarizing article: {e}")
        return "Unable to summarize this article at the moment."
