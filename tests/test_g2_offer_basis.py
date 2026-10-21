"""I4: an approved offer is bound to the exact change request it answers (basis_event_id)."""
import sqlite3
import unittest
from datetime import date

from dashboard import n8n_client, state_store as ss
from test_g2_guard import OK, GuardCase  # tests/ is on sys.path under discover
from test_g2_n8n_client import reply


class TestOfferBasis(GuardCase):
    def requested_offer(self):
        """Call on 02/01 asks to change; offer proposed and approved on 03/01."""
        call = self.call()
        self.run_(call, as_of=date(2017, 1, 2), outcome='requested_change')
        offer = ss.create_action(self.con, 'offer_change', booking_id=22005, decision_at='2017-01-03')
        ss.decide_action(self.con, offer, 'RM', True)
        return call, offer

    def offer(self, when):
        return ss.create_action(self.con, 'offer_change', booking_id=22005, decision_at=when)

    def test_offer_records_the_change_request_it_answers(self):
        _, offer = self.requested_offer()
        basis = ss.get_action(self.con, offer)['basis_event_id']
        self.assertEqual(basis, ss.guest_responses(self.con, 22005)[-1]['seq'])
        self.run_(offer, as_of=date(2017, 1, 4), outcome='confirmed')  # same request: executes

    def test_new_change_request_after_confirmation_blocks_the_old_offer(self):
        """The case left open in review: confirmed, then a NEW change request."""
        call, offer = self.requested_offer()
        ss.record_response(self.con, call, response='confirmed', response_at='2017-01-04')
        ss.record_response(self.con, call, response='requested_change', response_at='2017-01-05')
        with self.assertRaisesRegex(ValueError, 'newer change request'):
            self.run_(offer, as_of=date(2017, 1, 6), outcome='confirmed')
        post = reply(True)
        with self.assertRaisesRegex(ValueError, 'newer change request'):
            n8n_client.send_action(self.con, offer, webhook='http://unused.test', as_of=date(2017, 1, 6),
                                   outcome='confirmed', precheck=OK, post=post)
        post.assert_not_called()
        self.assertIsNone(ss.get_actions(self.con, 22005)[0]['intervention_id'])

    def test_repeated_change_request_also_needs_a_new_offer(self):
        call, offer = self.requested_offer()
        ss.record_response(self.con, call, response='requested_change', response_at='2017-01-04')
        with self.assertRaisesRegex(ValueError, 'newer change request'):
            self.run_(offer, as_of=date(2017, 1, 5), outcome='confirmed')

    def test_new_offer_for_the_new_request_can_be_proposed_and_approved(self):
        call, old = self.requested_offer()
        ss.record_response(self.con, call, response='requested_change', response_at='2017-01-04')
        new = self.offer('2017-01-04')  # the dead offer neither blocks nor counts toward the limit
        self.assertEqual(ss.get_action(self.con, new)['approval_status'], 'pending')
        with self.assertRaises(PermissionError):
            self.run_(new, as_of=date(2017, 1, 5), outcome='confirmed')  # still needs approval
        ss.decide_action(self.con, new, 'RM', True)
        self.run_(new, as_of=date(2017, 1, 5), outcome='confirmed')
        # The old offer stays dead (now also because the latest response is no change request).
        with self.assertRaisesRegex(ValueError, 'newer change request|I4'):
            self.run_(old, as_of=date(2017, 1, 5), outcome='confirmed')

    def test_still_one_live_offer_per_request(self):
        self.requested_offer()
        with self.assertRaisesRegex(ValueError, 'open offer_change|max 1'):
            self.offer('2017-01-03')

    def test_executed_offer_still_counts(self):
        call, offer = self.requested_offer()
        self.run_(offer, as_of=date(2017, 1, 4), outcome='no_response')
        ss.record_response(self.con, call, response='requested_change', response_at='2017-01-05')
        with self.assertRaisesRegex(ValueError, 'max 1'):
            self.offer('2017-01-05')

    def test_basis_is_required_and_immutable(self):
        _, offer = self.requested_offer()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
            self.con.execute('UPDATE action_plan SET basis_event_id = basis_event_id + 1 WHERE action_id = ?',
                             (offer,))
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'CHECK'):
            self.con.execute("INSERT INTO action_plan (booking_id, action_type, rule_id, decision_at, "
                             "needs_approval, approval_status, created_at) VALUES "
                             "(1, 'offer_change', 'I4', '2017-01-01', 1, 'pending', '2026')")

    def test_payload_carries_basis_and_latest_event(self):
        _, offer = self.requested_offer()
        post = reply(True)
        n8n_client.send_action(self.con, offer, webhook='http://unused.test', as_of=date(2017, 1, 4),
                               outcome='confirmed', precheck=OK, post=post)
        body = post.call_args.kwargs['json']
        self.assertEqual(body['offer_basis_event_id'], body['latest_response_event_id'])


if __name__ == '__main__':
    unittest.main()
