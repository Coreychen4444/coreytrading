from copy import deepcopy

import pytest

from quant.core.agent_contracts import canonical_hash
from quant.valuation.releases import EVIDENCE, append_event, resolve

NOW = '2026-09-08T12:00:00+00:00'


def fixture():
    store = {}
    def put(row):
        digest = canonical_hash(row); store[digest] = row; return digest
    evidence = {category: put({'category': category, 'ticker': 'SYN', 'kind': 'fair_value',
                 'input_hash': 'a'*64, 'code_hash': 'b'*64, 'completed_at': '2026-09-08T08:00:00+00:00',
                 'conclusion': 'passed', 'basis': 'Synthetic independent evidence for control test only'})
                for category in EVIDENCE}
    packet = {'ticker': 'SYN', 'kind': 'fair_value', 'model_version': 'company-test',
              'authority': 'analysis_only', 'author_id': 'synthetic-author',
              'created_at': '2026-09-08T09:00:00+00:00', 'valid_until': '2026-09-09T09:00:00+00:00',
              'input_hash': 'a'*64, 'code_hash': 'b'*64, 'evidence': evidence,
              'output': {'low': 10, 'point': 20, 'high': 30, 'currency': 'USD',
                         'value_at': NOW, 'interval_kind': 'conditional_scenarios', 'basis': 'Synthetic'}}
    digest = put(packet)
    review = put({'candidate_hash': digest, 'decision': 'accepted', 'reviewer_id': 'synthetic-reviewer',
                  'scope': 'fair_value', 'rationale': 'Synthetic test only', 'reviewed_at': '2026-09-08T10:00:00+00:00'})
    event = {'event_id': 'one', 'action': 'promote', 'ticker': 'SYN', 'kind': 'fair_value',
             'at': '2026-09-08T11:00:00+00:00', 'reason': 'Synthetic release test',
             'candidate_hash': digest, 'review_hash': review}
    return append_event([], event, expected_head=None), store


def select(events, store, **kw):
    args = dict(ticker='SYN', kind='fair_value', as_of=NOW, current_input_hash='a'*64,
                current_code_hash='b'*64, enabled=True)
    args.update(kw)
    return resolve(events, store, **args)


def test_exact_release_and_default_disabled():
    events, store = fixture()
    assert select(events, store)['output']['point'] == 20
    assert select(events, store, enabled=False)['status'] == 'withheld'
    assert select(events, store, ticker='OTHER')['status'] == 'withheld'
    assert select(events, store, kind='market_target_12m')['status'] == 'withheld'


@pytest.mark.parametrize('kw', [{'current_input_hash': 'c'*64}, {'current_code_hash': 'c'*64},
                               {'as_of': '2026-09-09T09:00:00+00:00'},
                               {'as_of': '2026-09-08T10:30:00+00:00'}])
def test_stale_changed_or_future_release_withheld(kw):
    assert select(*fixture(), **kw)['status'] == 'withheld'


def test_revocation_never_falls_back_and_requires_matching_company():
    events, store = fixture()
    second = {k: v for k, v in events[0].items() if k not in ('event_hash', 'previous_hash', 'sequence')}
    second.update(event_id='two', at=NOW)
    events = append_event(events, second, expected_head=events[-1]['event_hash'])
    revoke = {'event_id': 'three', 'action': 'revoke', 'ticker': 'SYN', 'kind': 'fair_value',
              'at': NOW, 'reason': 'failed evidence', 'release_id': 'two'}
    with pytest.raises(ValueError):
        append_event(events, dict(revoke, ticker='OTHER'), expected_head=events[-1]['event_hash'])
    events = append_event(events, revoke, expected_head=events[-1]['event_hash'])
    assert select(events, store)['reason'] == 'current_release_revoked'
    assert len(events) == 3  # Prior release remains auditable, not automatically served.


def test_cas_and_tampered_history():
    events, store = fixture()
    with pytest.raises(ValueError, match='changed'):
        append_event(events, {}, expected_head=None)
    events[0]['reason'] = 'changed'
    assert select(events, store)['status'] == 'withheld'


def test_artifact_edit_or_missing_evidence_disables_existing_release():
    events, store = fixture()
    original = deepcopy(store)
    store[events[0]['candidate_hash']]['output']['point'] = 25
    assert select(events, store)['status'] == 'withheld'
    evidence = next(iter(original[events[0]['candidate_hash']]['evidence'].values()))
    del original[evidence]
    assert select(events, original)['status'] == 'withheld'


def test_new_invalid_candidate_cannot_fall_back_to_prior_success():
    events, store = fixture()
    new = {k: v for k, v in events[0].items() if k not in ('event_hash', 'previous_hash', 'sequence')}
    new.update(event_id='two', candidate_hash='f'*64, at=NOW)
    events = append_event(events, new, expected_head=events[-1]['event_hash'])
    assert select(events, store)['status'] == 'withheld'


@pytest.mark.parametrize('change', ['self_review', 'descriptive_evidence', 'wrong_scope', 'future_evidence'])
def test_rehashed_documents_do_not_bypass_acceptance_contract(change):
    events, store = fixture()
    event = {k: v for k, v in events[0].items() if k not in ('event_hash', 'previous_hash', 'sequence')}
    packet = deepcopy(store[event['candidate_hash']])
    review = deepcopy(store[event['review_hash']])
    if change == 'self_review':
        review['reviewer_id'] = packet['author_id']
    elif change == 'wrong_scope':
        review['scope'] = 'market_target_12m'
    else:
        category = 'statistical_acceptance'
        item = deepcopy(store[packet['evidence'][category]])
        if change == 'descriptive_evidence':
            item['conclusion'] = 'not_determined'
        else:
            item['completed_at'] = NOW
        digest = canonical_hash(item); store[digest] = item
        packet['evidence'][category] = digest
    event['candidate_hash'] = canonical_hash(packet); store[event['candidate_hash']] = packet
    review['candidate_hash'] = event['candidate_hash']
    event['review_hash'] = canonical_hash(review); store[event['review_hash']] = review
    events = append_event([], event, expected_head=None)
    assert select(events, store)['status'] == 'withheld'


def test_reactivation_requires_fresh_review_and_still_valid_artifacts():
    events, store = fixture()
    original = {k: v for k, v in events[0].items() if k not in ('event_hash', 'previous_hash', 'sequence')}
    revoke = {'event_id': 'withdraw', 'action': 'revoke', 'ticker': 'SYN', 'kind': 'fair_value',
              'at': '2026-09-08T11:20:00+00:00', 'reason': 'Synthetic drill', 'release_id': 'one'}
    events = append_event(events, revoke, expected_head=events[-1]['event_hash'])
    promotion = dict(original, event_id='restore', at=NOW)
    stale = append_event(events, promotion, expected_head=events[-1]['event_hash'])
    assert select(stale, store)['status'] == 'withheld'
    review = deepcopy(store[original['review_hash']])
    review['reviewed_at'] = '2026-09-08T11:40:00+00:00'
    review['rationale'] = 'Synthetic fresh review after withdrawal'
    promotion['review_hash'] = canonical_hash(review); store[promotion['review_hash']] = review
    restored = append_event(events, promotion, expected_head=events[-1]['event_hash'])
    assert select(restored, store)['status'] == 'released'
    assert select(restored, store, current_input_hash='c'*64)['status'] == 'withheld'
