"""Plain words for the dashboard: what staff read instead of the project's internal codes.

The codes (F, G1, I1-I6, G2, G3, phone_call, no_response ...) stay in the data, the store and
the CSV downloads, and are shown next to the plain name where a reader of the report needs to
match them. friendly_error() turns the store's refusals (state_store / database guards) into a
sentence that says what happened and what to do next; the rules themselves are unchanged.
"""
from __future__ import annotations

import re

# What each rule does, in the words of the person who acts on it.
RULES = {
    'I1': ('Confirmation email', 'Lower risk: a confirmation email now and a reminder 7 days before arrival.'),
    'I2': ('Reminder plan', 'Medium risk: reminders 30 and 14 days before arrival.'),
    'I3': ('Personal call', 'Highest risk with time left: a staff call within 7 days of the score.'),
    'I4': ('Change offer', 'After a call where the guest asked to change the stay; needs approval.'),
    'I5': ('Too close to call', 'High risk but arrives within 7 days: no contact, counted in capacity planning.'),
    'I6': ('Deposit request', 'High-risk booking without deposit, only under a written deposit policy; '
                              'needs approval.'),
    'G2': ('Group block', 'Group bookings: handled per stay date and room type in capacity planning.'),
    'G3': ('Overbooking', 'Switched off by policy in this version.'),
    'G5': ('Price change', 'Per stay date and room type; needs approval.'),
}
SYSTEMS = {'F': 'risk model', 'G1': 'contact policy', 'G2': 'action system', 'n8n': 'automation (n8n)'}

ACTIONS = {
    'confirm_email': 'Confirmation email', 'reminder': 'Reminder', 'phone_call': 'Call guest',
    'offer_change': 'Change offer', 'request_deposit': 'Deposit request',
    'capacity_allocation': 'Hold rooms back', 'resale': 'Release rooms for resale',
    'price_adjustment': 'Adjust price', 'overbooking': 'Overbooking (disabled)',
}
OUTCOMES = {'no_response': 'No answer', 'confirmed': 'Guest confirmed',
            'requested_change': 'Guest asked to change', 'cancelled': 'Guest cancelled',
            'completed': 'Done'}
APPROVALS = {'auto': 'No approval needed', 'pending': 'Awaiting approval', 'approved': 'Approved',
             'rejected': 'Rejected'}
SCHEDULE = {'scheduled': 'Planned', 'manual_capacity_review': 'No call slot: decide manually',
            'buffer_only': 'No contact (capacity planning)'}
TIERS = {'high': 'High', 'medium': 'Medium', 'low': 'Low'}
ATTEMPTS = {'accepted': 'Sent', 'refused': 'Refused by automation', 'error': 'Failed'}


def rule(code, with_code: bool = True) -> str:
    """'Personal call (I3)'; unknown codes are returned as they are."""
    if code is None or code != code:  # None / NaN
        return '—'
    name = RULES.get(str(code), (str(code), ''))[0]
    return f'{name} ({code})' if with_code and str(code) in RULES else name


def rule_help(code) -> str:
    return RULES.get(str(code), ('', ''))[1]


def action(code) -> str:
    return ACTIONS.get(code, str(code))


def outcome(code) -> str:
    return OUTCOMES.get(code, str(code))


def approval(code) -> str:
    return APPROVALS.get(code, str(code))


def schedule(code) -> str:
    return SCHEDULE.get(code, str(code))


_CHECKS = ('Tick the three checks before saving: the guest has not opted out of contact, there is '
           'no other open action of this type, and the booking is still active.')
# (pattern on the store's message, plain sentence; groups fill the {n} fields)
_ERRORS = [
    (r'Execution-time checks not passed', _CHECKS),
    (r'Not due yet: scheduled for (\d{4}-\d{2}-\d{2})', 'Not due yet: planned for {0}.'),
    (r'Cannot execute before the decision date (\d{4}-\d{2}-\d{2})', 'Not decided yet on this date: it was decided on {0}.'),
    (r'Too late: arrival / stay date (\d{4}-\d{2}-\d{2}) reached', 'Too late: the arrival / stay date {0} has been reached, '
                                                    'so no contact or change is possible.'),
    (r'Too late on (\d{4}-\d{2}-\d{2}): arrival / stay date is (\d{4}-\d{2}-\d{2})', 'Too late on {0}: the arrival / stay date is {1}.'),
    (r'Too late on (\d{4}-\d{2}-\d{2}): arrival is (\d{4}-\d{2}-\d{2})', 'Too late on {0}: the guest arrives on {1}.'),
    (r'Outside G1 call window', 'The call window has passed: calls are made within 7 days of the risk '
                                'score. Close this call without contact; a reminder can then replace it.'),
    (r'G1 call needs at least (\d+) days before arrival', 'Too close to arrival for a planned call (needs at '
                                                          'least {0} days). Close it without contact.'),
    (r'Daily call capacity reached', "Today's call limit is reached. Calls left for today can be made on the "
                                     'next day.'),
    (r'Stopped: guest already responded \((\w+)\)', 'The guest already answered ({0}): no more follow-ups '
                                                    'for this booking.'),
    (r'Follow-ups stopped: guest already responded \((\w+)\)', 'The guest already answered ({0}): no more '
                                                               'follow-ups for this booking.'),
    (r'Booking was cancelled', 'The guest cancelled this booking: no further actions.'),
    (r'Action is pending/rejected', 'Needs approval first: a revenue manager must approve this action.'),
    (r'action lacks required approval', 'Needs approval first: a revenue manager must approve this action.'),
    (r'already been executed', 'Already done: this action was recorded before.'),
    (r'closed without contact \((.*)\)', 'This action was closed without contact ({0}).'),
    (r'Back-dated: (.+) already has an event on (\d{4}-\d{2}-\d{2})', 'This {0} already has an entry on {1}: entries only '
                                                       'move forward in time.'),
    (r'call #(\d+) is due', 'Call #{0} for this booking is due: make the call first, the message waits.'),
    (r'a call was made to this booking today', 'A call was made to this booking today: no automatic message '
                                               'on the same day.'),
    (r'a (\w+) was already sent today', 'A {0} went to this guest today: make the call on another day.'),
    (r'Enter a reviewer name', 'Enter your name as reviewer.'),
    (r'Enter who changes the action and why', 'Enter your name and why the action is changed.'),
    (r'Enter who closes the action and why', 'Enter your name and why the action is closed.'),
    (r'Only auto or approved actions can be closed', 'An action awaiting approval is rejected, '
                                                     'not closed.'),
    (r'Give a reason for rejecting', 'Give a reason for rejecting the recommendation.'),
    (r'Booking (\d+) was already (approved|rejected) by (.+) on (\d{4}-\d{2}-\d{2})',
     'Already reviewed: booking {0} was {1} by {2} on {3}.'),
    (r'has no G1 plan to review', 'This booking has no planned contact to approve: decide it manually.'),
    (r'Not known yet on (\d{4}-\d{2}-\d{2}): F (?:decides|scores) this booking on (\d{4}-\d{2}-\d{2})',
     'Not scored yet on {0}: the risk model scores this booking on {1}.'),
    (r'Limit reached: max (\d+) (\w+) per booking', 'Limit reached: at most {0} of this action per booking.'),
    (r'An open (\w+) action \(#(\d+)\) already exists', 'This booking already has an open action of this type '
                                                       '(#{1}). Finish or close it first.'),
    (r'I6 deposit request is off by default', 'Deposit requests (I6) need a written deposit policy: switch it '
                                              'on first.'),
    (r'I6 needs a high-tier booking \(this one: (\w+)\)', 'A deposit request (I6) is only for high-risk '
                                                          'bookings (this one is {0}).'),
    (r'I6 applies to No Deposit bookings', 'A deposit request (I6) is only for bookings without a deposit.'),
    (r'I6 needs the written-policy reference', 'A deposit request (I6) needs the clause of the written policy '
                                               'that applies.'),
    (r'I4 offer_change only follows a call', 'A change offer (I4) is only possible after a call where the '
                                             "guest's latest answer was a change request."),
    (r'newer change request', 'The guest made a newer change request after this offer (I4): create and '
                              'approve a new offer.'),
    (r'overbooking is disabled', 'Overbooking is switched off by policy.'),
    (r'G1 plan of this booking was rejected', "This booking's recommendation was rejected at review: only "
                                              'manual actions can be added.'),
    (r'Only pending actions can be reviewed', 'Already decided: only actions awaiting approval can be '
                                              'approved or rejected.'),
    (r'database is locked', 'Another user is saving at the same moment: try again in a few seconds.'),
    (r'Booking ID is not in', 'This booking is not in the 2017 queue.'),
]


def friendly_error(message) -> str:
    """A refusal from the store as a plain sentence with the next step; unknown ones unchanged."""
    text = str(message)
    for pattern, template in _ERRORS:
        m = re.search(pattern, text)
        if m:
            return template.format(*m.groups())
    return text
