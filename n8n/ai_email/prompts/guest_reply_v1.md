TASK: classify the GUEST MESSAGE and write a REPLY (email_type guest_reply).
1. Choose intent and certainty with the intent guide. Copy 1 to 5 exact quotes into evidence_quotes.
2. Extract every requested change into requested_changes with exact quotes. Weekday names and relative expressions are not one date: proposed_value null, ambiguity_reason says why.
3. Reply to what the guest wrote. For a change or cancellation request, acknowledge it and say the reservations team will check it before anything is changed; nothing is changed or cancelled yet. When a date or decision is missing, ask for it (requires_clarification true). Do not promise availability, fees, refunds or a new date.
4. Cite guest_message and the booking source in source_refs, and policy:changes_need_staff when you mention verification.
