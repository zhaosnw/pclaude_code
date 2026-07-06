"""
Common constants.

Port of: src/constants/common.ts
"""

from datetime import date


def get_local_iso_date() -> str:
    return date.today().isoformat()


def get_local_month_year() -> str:
    """Return current month and year, e.g. 'March 2026'."""
    today = date.today()
    return today.strftime("%B %Y")
