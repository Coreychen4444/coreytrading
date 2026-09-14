"""Exact-artifact valuation release selection, with append-only revocation.

Pure functions; 零授权效力. The caller owns trusted journal/review storage and
must supply current input/code hashes and time. Hashes bind content, not reviewer
identity or evidence truth. This does not approve a methodology or replace the
production global publication hold. No fallback to a previous release.
"""
from __future__ import annotations

from copy import deepcopy

from quant.core.agent_contracts import canonical_hash
from quant.valuation.company_model import stamp, number

KINDS = frozenset({'fair_value', 'market_target_12m'})
EVIDENCE = frozenset({'source_audit', 'methodology', 'point_in_time_validation',
                      'statistical_acceptance', 'rollback_drill'})


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _artifact(store, digest):
    if not _digest(digest) or digest not in store or canonical_hash(store[digest]) != digest:
        raise ValueError('missing or changed release artifact')
    return store[digest]


def journal(events):
    """Validate complete ordered history, including revocations of old releases."""
    previous, ids, promoted, revoked, last = None, set(), {}, set(), None
    for index, event in enumerate(events):
        body = {k: v for k, v in event.items() if k != 'event_hash'}
        when = stamp(event['at'])
        if (event['event_hash'] != canonical_hash(body) or event['previous_hash'] != previous
                or event['sequence'] != index or not event['event_id'] or event['event_id'] in ids
                or (last is not None and when < last)):
            raise ValueError('broken release journal chain/order')
        if event['kind'] not in KINDS or not event['ticker'] or not event.get('reason'):
            raise ValueError('explicit company/output/reason required')
        if event['action'] == 'promote':
            if not all(_digest(event.get(k)) for k in ('candidate_hash', 'review_hash')):
                raise ValueError('promotion requires exact candidate and review')
            promoted[event['event_id']] = event
        elif event['action'] == 'revoke':
            target = promoted.get(event['release_id'])
            if (target is None or event['release_id'] in revoked
                    or (target['ticker'], target['kind']) != (event['ticker'], event['kind'])):
                raise ValueError('revocation must identify an unrevoked matching release')
            revoked.add(event['release_id'])
        else:
            raise ValueError('unknown release action')
        ids.add(event['event_id']); previous = event['event_hash']; last = when
    return promoted, revoked


def append_event(events, event, *, expected_head):
    """Pure compare-and-swap preparation. A persistent writer must lock around it."""
    journal(events)
    head = events[-1]['event_hash'] if events else None
    if head != expected_head:
        raise ValueError('release journal changed; re-read before submission')
    if set(event) & {'event_hash', 'previous_hash', 'sequence'}:
        raise ValueError('caller cannot supply journal bookkeeping')
    row = deepcopy(event)
    row.update(sequence=len(events), previous_hash=head)
    row['event_hash'] = canonical_hash(row)
    result = deepcopy(events) + [row]
    journal(result)
    return result


def resolve(events, artifacts, *, ticker, kind, as_of, current_input_hash,
            current_code_hash, enabled=False):
    """Select one current exact release; never use a stale previous success.

    `enabled` is a trusted host rollout control, never read from company input.
    Statistical/reviewer findings are independently produced artifacts, not
    booleans copied from successful unit tests or descriptive metric receipts.
    """
    hold = lambda reason: {'status': 'withheld', 'reason': reason, 'ticker': ticker,
                           'kind': kind, 'authority': 'analysis_only'}
    if not enabled:
        return hold('release_channel_disabled')
    try:
        if kind not in KINDS:
            raise ValueError('unsupported output kind')
        promoted, revoked = journal(events)
        now = stamp(as_of)
        if any(stamp(e['at']) > now for e in events):
            raise ValueError('journal contains future event')
        candidates = [e for e in promoted.values() if (e['ticker'], e['kind']) == (ticker, kind)]
        if not candidates:
            return hold('no_release')
        release = candidates[-1]
        if release['event_id'] in revoked:
            return hold('current_release_revoked')
        packet = _artifact(artifacts, release['candidate_hash'])
        review = _artifact(artifacts, release['review_hash'])
        made, expires, reviewed, published = map(stamp, [packet['created_at'], packet['valid_until'],
                                                        review['reviewed_at'], release['at']])
        if not made <= reviewed <= published <= now < expires:
            raise ValueError('release expired or chronology invalid')
        earlier = [e for e in events[:release['sequence']]
                   if (e['ticker'], e['kind']) == (ticker, kind)]
        if any(e.get('candidate_hash') == release['candidate_hash'] for e in earlier):
            if reviewed <= max(stamp(e['at']) for e in earlier):
                raise ValueError('rollback/re-promotion requires a new review after prior release events')
        if (packet['ticker'], packet['kind']) != (ticker, kind):
            raise ValueError('candidate scope mismatch')
        if (packet['authority'] != 'analysis_only' or not packet['model_version'].startswith('company-')
                or not _digest(current_input_hash) or not _digest(current_code_hash)
                or packet['input_hash'] != current_input_hash or packet['code_hash'] != current_code_hash):
            raise ValueError('input/code/authority mismatch')
        if (review['candidate_hash'] != release['candidate_hash'] or review['decision'] != 'accepted'
                or review['reviewer_id'] == packet['author_id'] or not review['reviewer_id']
                or review['scope'] != kind or not review['rationale']):
            raise ValueError('independent exact-scope acceptance review required')
        evidence = packet['evidence']
        if set(evidence) != EVIDENCE or len(set(evidence.values())) != len(EVIDENCE):
            raise ValueError('complete distinct acceptance evidence required')
        for category, digest in evidence.items():
            item = _artifact(artifacts, digest)
            if (item['category'] != category or item['ticker'] != ticker or item['kind'] != kind
                    or item['input_hash'] != current_input_hash or item['code_hash'] != current_code_hash
                    or stamp(item['completed_at']) > made or item['conclusion'] != 'passed'
                    or not item['basis']):
                raise ValueError('acceptance evidence scope/time/conclusion mismatch')
        # This is a reviewed report output, not a conversion of scenario extrema.
        payload = packet['output']
        low, point, high = [number(payload[k], k, minimum=0) for k in ('low', 'point', 'high')]
        if not low <= point <= high or payload['currency'] != 'USD' or not payload['basis']:
            raise ValueError('invalid released value range')
        if stamp(payload['value_at']) < made:
            raise ValueError('output valuation date predates candidate')
        if kind == 'market_target_12m':
            from quant.valuation.market_validation import target_close
            if stamp(payload['value_at']) != target_close(packet['created_at']):
                raise ValueError('market target must match fixed twelve-month horizon')
            if payload['interval_kind'] != 'predictive' or not 0 < number(payload['alpha'], 'alpha') < 1:
                raise ValueError('market interval must specify nominal predictive coverage')
        elif payload['interval_kind'] != 'conditional_scenarios':
            raise ValueError('intrinsic range must be identified as conditional scenarios')
        return {'status': 'released', 'authority': 'analysis_only', 'ticker': ticker, 'kind': kind,
                'release_id': release['event_id'], 'candidate_hash': release['candidate_hash'],
                'review_hash': release['review_hash'], 'model_version': packet['model_version'],
                'valid_until': packet['valid_until'], 'output': deepcopy(payload)}
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
        return hold(str(exc))
