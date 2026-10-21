// n8n Code node "Approval & execution-time guard". Mirrors dashboard/state_store.py.
const b = $input.first().json.body ?? {};
const ACTIONS = new Set(['confirm_email','reminder','phone_call','offer_change','request_deposit',
  'price_adjustment','capacity_allocation','resale','overbooking']);
const SENSITIVE = new Set(['offer_change','request_deposit','price_adjustment',
  'capacity_allocation','resale','overbooking']);
const GUEST = new Set(['confirm_email','reminder','phone_call','offer_change','request_deposit']);
const isInt = v => v !== null && v !== undefined && v !== '' && Number.isInteger(Number(v));
const reasons = [];
if (b.mode !== 'demo_no_guest_contact') reasons.push('mode must be demo_no_guest_contact');
if (!ACTIONS.has(b.action_type)) reasons.push('unknown action_type');
// This workflow version supports no operational G3 policy, even with approval.
if (b.action_type === 'overbooking') reasons.push('G3 overbooking is disabled by policy');
if (!isInt(b.action_id)) reasons.push('action_id must be an integer');
const sensitive = SENSITIVE.has(b.action_type);
if (sensitive && b.needs_approval !== true) reasons.push('sensitive action must need approval');
const status = String(b.approval_status ?? '');
const approved = status === 'approved' || (status === 'auto' && !sensitive && b.needs_approval === false);
if (!approved) reasons.push('action is not approved (pending, rejected or invalid auto)');
if (GUEST.has(b.action_type)) {
  if (!isInt(b.booking_id)) reasons.push('booking_id required for a guest action');
  const p = b.precheck ?? {};
  for (const k of ['consent_confirmed', 'no_open_conflict', 'booking_active']) {
    if (p[k] !== true) reasons.push(`execution-time check not passed: ${k}`);
  }
} else if (!b.stay_date || !b.room_type) {
  reasons.push('stay_date and room_type required for a date x room-type action');
}
if (b.scheduled_at && (!b.as_of || String(b.as_of) < String(b.scheduled_at))) {
  reasons.push(`not due yet (scheduled ${b.scheduled_at})`);
}
const dayNumber = value => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return NaN;
  const parsed = Date.parse(value + 'T00:00:00Z');
  return Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === value
    ? parsed / 86400000 : NaN;
};
if (b.action_type === 'phone_call') {
  const c = b.execution_policy?.call_schedule ?? {};
  const limitsOK = ['start_day', 'window_days', 'min_days_before_arrival']
    .every(k => Number.isInteger(c[k])) &&
    c.start_day >= 0 && c.start_day < c.window_days && c.min_days_before_arrival >= 8;
  const today = dayNumber(b.as_of), scored = dayNumber(b.known_from);
  const arrival = dayNumber(b.arrival_date), decision = dayNumber(b.decision_at);
  if (!limitsOK || ![today, scored, arrival, decision].every(Number.isFinite)) {
    reasons.push('G1 phone call requires valid policy, original score time, decision and arrival dates');
  } else {
    if (today < decision || scored > decision) reasons.push('invalid phone-call decision timeline');
    if (today - scored < c.start_day || today - scored >= c.window_days) {
      reasons.push('outside G1 call window: manual review required');
    }
    if (arrival - today < c.min_days_before_arrival) {
      reasons.push('insufficient days before arrival for G1 call: manual review required');
    }
  }
}
// Trusted state sent by the G2 service (production: read it from PostgreSQL by action_id).
if (b.already_executed !== false) reasons.push('action already executed or state unknown');
const FOLLOW_UPS = new Set(['confirm_email', 'reminder', 'phone_call']);
const response = b.guest_response ?? null;
if (response === 'cancelled' || (response && FOLLOW_UPS.has(b.action_type))) {
  reasons.push(`guest already responded (${response}): follow-ups stopped`);
}
if (b.action_type === 'offer_change' &&
    (response !== 'requested_change' || b.guest_response_action_type !== 'phone_call')) {
  reasons.push('I4 needs the latest response to be a change request from a phone call');
}
if (b.action_type === 'offer_change' &&
    (!isInt(b.offer_basis_event_id) || Number(b.offer_basis_event_id) !== Number(b.latest_response_event_id))) {
  reasons.push('I4 offer was approved for an earlier change request: a new offer and approval are needed');
}
return [{ json: { accepted: reasons.length === 0, reasons, body: b } }];
