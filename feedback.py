"""Send Feedback: opens the user's mail app with a draft to the developer.

Nothing is sent by the widget itself. The draft holds no spending data; the
user reads it, edits it, and sends it from their own mail app.
"""
from urllib.parse import quote

FEEDBACK_EMAIL = "jordan@moneyguilt.com"
SUBJECT = "Money Guilt feedback"
BODY = ("What's on your mind? (Bug, idea, or anything else.)\n\n\n\n"
        "-- Please don't include account numbers or other private details. --")


def feedback_url():
    return f"mailto:{FEEDBACK_EMAIL}?subject={quote(SUBJECT)}&body={quote(BODY)}"
