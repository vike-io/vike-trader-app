"""Title normalization, category bucketing, and currency→country/ISO mapping.

Kept deliberately small and data-driven so it's cheap to extend as coverage grows.
"""
from __future__ import annotations

import re

# qualifiers that distinguish releases but not the underlying indicator identity
_QUALIFIERS = re.compile(
    r"\b(yoy|mom|qoq|wow|flash|prel|prelim|preliminary|final|adv|advance|"
    r"2nd est|3rd est|s\.a\.|n\.s\.a\.)\b",
    re.IGNORECASE,
)


def normalize_title(title: str) -> str:
    t = _QUALIFIERS.sub("", title or "")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


# ordered (keyword, category) — first match wins
_CATEGORY_RULES: list[tuple[str, str]] = [
    ("payroll", "employment"), ("unemployment", "employment"),
    ("jobless", "employment"), ("employment", "employment"), ("jolts", "employment"),
    ("inflation", "inflation"), ("cpi", "inflation"), ("ppi", "inflation"),
    ("pce price", "inflation"),
    ("interest rate", "rates"), ("rate decision", "rates"), ("fed ", "rates"),
    ("ecb ", "rates"), ("boe ", "rates"), ("fomc", "rates"),
    ("gdp", "gdp"),
    ("balance of trade", "trade"), ("exports", "trade"), ("imports", "trade"),
    ("current account", "trade"),
    ("housing", "housing"), ("home sales", "housing"), ("building permits", "housing"),
    ("mortgage", "housing"),
]


def categorize(title: str) -> str:
    t = (title or "").lower()
    for kw, cat in _CATEGORY_RULES:
        if kw in t:
            return cat
    return "other"


# ForexFactory currency code → (display country, ISO-3166 alpha-2 lowercase for flags)
CURRENCY_COUNTRY: dict[str, tuple[str, str]] = {
    "USD": ("United States", "us"), "EUR": ("European Union", "eu"),
    "GBP": ("United Kingdom", "gb"), "JPY": ("Japan", "jp"),
    "AUD": ("Australia", "au"), "NZD": ("New Zealand", "nz"),
    "CAD": ("Canada", "ca"), "CHF": ("Switzerland", "ch"),
    "CNY": ("Mainland China", "cn"), "INR": ("India", "in"),
    "BRL": ("Brazil", "br"), "ZAR": ("South Africa", "za"),
    "KRW": ("South Korea", "kr"), "MXN": ("Mexico", "mx"),
    "RUB": ("Russia", "ru"), "TRY": ("Turkey", "tr"),
    "IDR": ("Indonesia", "id"), "SAR": ("Saudi Arabia", "sa"),
    "SGD": ("Singapore", "sg"), "HKD": ("Hong Kong", "hk"),
    "SEK": ("Sweden", "se"), "NOK": ("Norway", "no"),
}


def currency_country(currency: str) -> tuple[str, str]:
    return CURRENCY_COUNTRY.get((currency or "").upper(), (currency, ""))
