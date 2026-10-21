"""Render the final email of a draft revision.

The model (or a fixture / staff) writes the wording; the facts come from the source context:
- placeholders [[booking.booking_id]], [[booking.arrival_date]], [[booking.room_type_code]],
  [[reminder.scheduled_at]], [[hotel.name]] are replaced from the context only (allowlist);
  any other [[...]] stays unresolved and blocks sending;
- a facts block (booking reference, arrival date, room-type code) is generated from the context
  for every email type: the model never writes those values itself;
- the [DEMO] subject prefix and the DEMO header / footer are added here, before the content hash,
  so the approved hash is the hash of exactly what is sent. Staff edit the wording, never the
  facts block or the DEMO lines.
"""
from __future__ import annotations

import re
from datetime import date

from dashboard.ai_email import contract as c

PLACEHOLDER = re.compile(r'\[\[\s*([a-z_]+(?:\.[a-z_]+)*)\s*\]\]')
DEMO_PREFIX = '[DEMO] '
MONTH_EN = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
            'September', 'October', 'November', 'December')

TEXT = {
    'en': {
        'header': '[DEMO — test email from the City Hotel student project. It is not sent to a real '
                  'guest and no booking was changed.]',
        'facts_title': 'Booking details (hotel record, demo scenario):',
        'ref': 'Booking reference', 'arrival': 'Arrival date', 'room': 'Room type (code)',
        'footer': '— DEMO test message. Historical booking scenario (2017); the hotel\'s current '
                  'reservation system was not checked.',
    },
    'vi': {
        'header': '[DEMO — email thử nghiệm của dự án sinh viên City Hotel. Không gửi cho khách thật '
                  'và không thay đổi đặt phòng nào.]',
        'facts_title': 'Thông tin đặt phòng (dữ liệu khách sạn, kịch bản demo):',
        'ref': 'Mã đặt phòng', 'arrival': 'Ngày đến', 'room': 'Loại phòng (mã)',
        'footer': '— Email thử nghiệm DEMO. Kịch bản đặt phòng lịch sử (2017); chưa kiểm tra hệ '
                  'thống đặt phòng hiện tại của khách sạn.',
    },
}


def format_date(iso: str | None, language: str) -> str | None:
    if not iso:
        return None
    try:
        d = date.fromisoformat(iso)
    except ValueError:  # invalid source date: left out (and blocked by validate.fact_blockers)
        return None
    return f'{d.day} {MONTH_EN[d.month - 1]} {d.year}' if language == 'en' else d.strftime('%d/%m/%Y')


def placeholder_values(context: dict) -> dict[str, str]:
    """The allowlist: placeholder -> value from the context. Missing facts are absent."""
    lang = context['language']
    b = context['booking']
    values = {'hotel.name': context.get('hotel_name') or c.HOTEL_NAME}
    if b.get('booking_id') is not None:
        values['booking.booking_id'] = str(b['booking_id'])
    if format_date(b.get('arrival_date'), lang):
        values['booking.arrival_date'] = format_date(b['arrival_date'], lang)
    if b.get('room_type_code'):
        values['booking.room_type_code'] = b['room_type_code']
    if context['email_type'] == 'reminder':
        r = c.next_reminder(context)
        if r:
            values['reminder.scheduled_at'] = format_date(r['scheduled_at'], lang)
    return values


ALLOWED_PLACEHOLDERS = ('booking.booking_id', 'booking.arrival_date', 'booking.room_type_code',
                        'reminder.scheduled_at', 'hotel.name')


def resolve(text: str, context: dict) -> tuple[str, list[str]]:
    """Replace allowlisted placeholders; return (text, unresolved placeholder names)."""
    values = placeholder_values(context)
    unresolved = []

    def sub(m):
        name = m.group(1)
        if name in values:
            return values[name]
        unresolved.append(name)
        return m.group(0)

    return PLACEHOLDER.sub(sub, text or ''), unresolved


def facts_block(context: dict) -> str:
    t = TEXT[context['language']]
    b = context['booking']
    lines = [t['facts_title']]
    if b.get('booking_id') is not None:
        lines.append(f"- {t['ref']}: {b['booking_id']}")
    if format_date(b.get('arrival_date'), context['language']):
        lines.append(f"- {t['arrival']}: {format_date(b['arrival_date'], context['language'])}")
    if b.get('room_type_code'):
        lines.append(f"- {t['room']}: {b['room_type_code']}")
    return '\n'.join(lines)


def render(context: dict, subject: str, body: str) -> dict:
    """Final subject / body of a revision and what could not be resolved."""
    t = TEXT[context['language']]
    subj, u1 = resolve(' '.join((subject or '').split()), context)
    text, u2 = resolve((body or '').strip(), context)
    if subj.startswith(DEMO_PREFIX.strip()):
        subj = subj[len(DEMO_PREFIX.strip()):].strip()
    rendered_subject = DEMO_PREFIX + subj
    rendered_body = '\n\n'.join([t['header'], text, facts_block(context), t['footer']])
    return {'subject': rendered_subject, 'body': rendered_body, 'prose': text,
            'unresolved': sorted(set(u1 + u2))}


def content_hash(subject: str, body: str) -> str:
    """sha256 of the exact email; same formula as g2_ai_v1.content_hash() in PostgreSQL."""
    return c.sha256(f'{subject}\n\x1f\n{body}')
