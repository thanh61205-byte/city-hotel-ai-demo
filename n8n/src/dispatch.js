// n8n Code node "Mock dispatch (nothing sent)". Builds what WOULD be sent; sends nothing.
const g = $input.first().json;
const b = g.body ?? {};
if (!g.accepted) {
  return [{ json: { action_id: b.action_id ?? null, accepted: false,
    status: 'BLOCKED_BY_GUARD', reasons: g.reasons, executed: false } }];
}
let dispatch;
switch (b.action_type) {
  case 'confirm_email':
  case 'reminder':
    dispatch = { type: 'email_preview',
      template: b.action_type === 'reminder' ? 'pre_arrival_reminder' : 'booking_confirmation',
      to: 'NOT SENT: the research data has no guest address',
      stop_if: 'guest responds or cancels' };
    break;
  case 'phone_call':
    dispatch = { type: 'staff_task', queue: 'Reservations follow-up',
      note: 'Call attempt (max 2 per booking); record the guest response' };
    break;
  default:
    dispatch = { type: 'approved_change_notice', to: 'Revenue manager (mock)',
      note: 'Approved change to carry out by staff; this workflow changes no PMS, price or inventory' };
}
return [{ json: { action_id: b.action_id, booking_id: b.booking_id ?? null, accepted: true,
  status: 'DEMO_ACCEPTED_NO_ACTION', dispatch, executed: false } }];
