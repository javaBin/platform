"""Shared constants across Lambda functions."""

USD_TO_NOK = 10.5  # Approximate exchange rate

RISK_EMOJIS = {
    "HIGH": ":red_circle:",
    "MEDIUM": ":large_orange_circle:",
    "LOW": ":yellow_circle:",
}


def risk_emoji(level):
    """Return Slack emoji for a risk level."""
    return RISK_EMOJIS.get(level, ":yellow_circle:")
