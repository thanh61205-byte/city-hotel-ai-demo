"""Date mentions in English / Vietnamese text, and relative dates as of the business date.

Two uses:
- fact check: every date written in a draft must be a source date (arrival, G1 schedule,
  business date) or a date the guest wrote explicitly (find_dates);
- relative dates ("Friday", "thứ Sáu", "next week", "tuần sau"): shown to staff with their
  possible readings from the message's BUSINESS date in the hotel time zone (never the
  computer's 2026 clock). A bare weekday or a week is not one date, so a requested change keeps
  proposed_value = null and the draft asks the guest (relative_mentions / is_explicit_date).
"""
from __future__ import annotations

import re
from datetime import date, timedelta

MONTHS = {m: i for i, ms in enumerate([
    ('january', 'jan'), ('february', 'feb'), ('march', 'mar'), ('april', 'apr'), ('may',),
    ('june', 'jun'), ('july', 'jul'), ('august', 'aug'), ('september', 'sep', 'sept'),
    ('october', 'oct'), ('november', 'nov'), ('december', 'dec')], start=1) for m in ms}
_MONTH_RE = '|'.join(sorted(MONTHS, key=len, reverse=True))

_PATTERNS = [
    ('ymd', re.compile(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b')),
    ('dmy', re.compile(r'\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b')),
    ('vi', re.compile(r'\bngày\s+(\d{1,2})\s*(?:/|tháng\s+)(\d{1,2})(?:\s*(?:/|năm\s+)(\d{4}))?', re.I)),
    ('d_mon', re.compile(rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\.?(?:,?\s+(\d{{4}}))?\b', re.I)),
    ('mon_d', re.compile(rf'\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?', re.I)),
    ('dm', re.compile(r'(?<![\d/.])(\d{1,2})/(\d{1,2})(?![\d/])')),
]


def _valid(y, m, d) -> bool:
    try:
        date(y or 2000, m, d)
    except ValueError:
        return False
    return True


def find_dates(text: str) -> list[dict]:
    """Date mentions: [{'text', 'year' (or None), 'month', 'day'}], each span counted once."""
    found, taken = [], []
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text or ''):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            g = m.groups()
            if kind == 'ymd':
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
            elif kind in ('dmy', 'vi'):
                d, mo, y = int(g[0]), int(g[1]), int(g[2]) if g[2] else None
            elif kind == 'd_mon':
                d, mo, y = int(g[0]), MONTHS[g[1].lower()], int(g[2]) if g[2] else None
            elif kind == 'mon_d':
                mo, d, y = MONTHS[g[0].lower()], int(g[1]), int(g[2]) if g[2] else None
            else:  # dm
                d, mo, y = int(g[0]), int(g[1]), None
            if not _valid(y, mo, d):
                continue
            taken.append((m.start(), m.end()))
            found.append({'text': m.group(0), 'year': y, 'month': mo, 'day': d})
    return found


def matches(mention: dict, allowed: set[date]) -> bool:
    """A mention matches a source date: same day, and same year when the mention has one."""
    return any(a.month == mention['month'] and a.day == mention['day']
               and (mention['year'] is None or a.year == mention['year']) for a in allowed)


def explicit_dates(text: str) -> list[date]:
    """Dates written with a year (unambiguous)."""
    return [date(m['year'], m['month'], m['day']) for m in find_dates(text) if m['year']]


WEEKDAYS = {
    'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3, 'friday': 4, 'saturday': 5,
    'sunday': 6, 'thứ hai': 0, 'thứ 2': 0, 'thứ ba': 1, 'thứ 3': 1, 'thứ tư': 2, 'thứ 4': 2,
    'thứ năm': 3, 'thứ 5': 3, 'thứ sáu': 4, 'thứ 6': 4, 'thứ bảy': 5, 'thứ 7': 5, 'chủ nhật': 6,
}
_WEEKDAY_RE = re.compile(r'(' + '|'.join(sorted(map(re.escape, WEEKDAYS), key=len, reverse=True)) + r')',
                         re.I)
_RELATIVE_RE = re.compile(r'\b(next week|this week|tomorrow|next month|in a few days|later)\b|'
                          r'(tuần sau|tuần tới|tuần này|ngày mai|tháng sau|vài ngày nữa)', re.I)


def relative_mentions(text: str, business_day: date) -> list[dict]:
    """Relative date expressions with their possible readings from the business day.

    A weekday gives two readings (this week's and next week's occurrence after the business
    day): never one date. Week / month expressions give no single date."""
    out = []
    for m in _WEEKDAY_RE.finditer(text or ''):
        wd = WEEKDAYS[m.group(1).lower()]
        first = business_day + timedelta(days=(wd - business_day.weekday()) % 7 or 7)
        out.append({'text': m.group(0), 'kind': 'weekday',
                    'readings': [first.isoformat(), (first + timedelta(days=7)).isoformat()],
                    'unique': False})
    for m in _RELATIVE_RE.finditer(text or ''):
        phrase = m.group(0).lower()
        if phrase in ('tomorrow', 'ngày mai'):
            out.append({'text': m.group(0), 'kind': 'day',
                        'readings': [(business_day + timedelta(days=1)).isoformat()], 'unique': True})
        else:
            out.append({'text': m.group(0), 'kind': 'period', 'readings': [], 'unique': False})
    return out


def is_explicit_date(value: str | None, message: str) -> bool:
    """proposed_value is acceptable only if it is a full date the guest wrote with a year."""
    if value is None:
        return True
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        return False
    return d in explicit_dates(message)
