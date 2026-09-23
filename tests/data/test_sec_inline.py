"""Prevent context mixing, silent numeric transformations, and duplicate selection."""
import pytest
from quant.data.sec_inline import parse, select

USD = {'numerator': ['iso4217:USD'], 'denominator': []}
PERIOD = {'start': '2026-04-27', 'end': '2026-07-26'}


def filing(facts, extra_context=''):
    return ('''<xbrli:context id="q"><xbrli:entity>
<xbrli:identifier scheme="http://www.sec.gov/CIK">0000000042</xbrli:identifier>
</xbrli:entity><xbrli:period><xbrli:startDate>2026-04-27</xbrli:startDate>
<xbrli:endDate>2026-07-26</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
''' + extra_context + facts).encode()


def fact(value='2,538', attrs='', context='q'):
    return f'<ix:nonFraction name="us-gaap:NetIncomeLoss" contextRef="{context}" unitRef="usd" scale="6" decimals="-6" format="ixt:num-dot-decimal" {attrs}>{value}</ix:nonFraction>'


def chosen(raw, **kwargs):
    return select(parse(raw), concept='us-gaap:NetIncomeLoss', entity='0000000042',
                  entity_scheme='http://www.sec.gov/CIK', period=PERIOD, unit=USD, **kwargs)


def test_scale_sign_exclusion_and_source_binding():
    raw = filing(fact('2,<span>538</span><ix:exclude>footnote</ix:exclude>', 'sign="-"'))
    r = chosen(raw)
    assert r['value'] == '-2538000000'
    assert not r['historical_availability_verified']
    assert r['source_sha256'] != chosen(raw + b' ')['source_sha256']


@pytest.mark.parametrize('attrs,value', [('xsi:nil="true"', '0'), ('continuedAt="c"', '1'),
                                         ('', '1,2'), ('sign="+"', '1'), ('', '(10)')])
def test_unknown_is_not_zero(attrs, value):
    r = chosen(filing(fact(value, attrs)))
    assert r['status'] == 'unknown' and r['value'] is None


def test_duplicate_values_reconcile_but_conflicts_and_unsupported_fail_closed():
    assert chosen(filing(fact() + fact()))['status'] == 'selected'
    assert chosen(filing(fact() + fact('2,539')))['reason'] == 'conflicting_matching_facts'
    assert chosen(filing(fact() + fact('—')))['reason'] == 'unsupported_matching_fact'


def test_period_and_entity_must_match():
    assert chosen(filing(fact()).replace(b'2026-04-27', b'2025-10-27'))['status'] == 'unknown'
    assert chosen(filing(fact()).replace(b'0000000042', b'0000000001'))['status'] == 'unknown'


def test_dimensional_facts_cannot_leak_into_consolidated():
    raw = filing(fact()).replace(b'</xbrli:entity>', b'''<xbrli:segment>
<xbrldi:explicitMember dimension="ex:SegmentAxis">ex:SegmentMember</xbrldi:explicitMember>
</xbrli:segment></xbrli:entity>''')
    assert chosen(raw)['status'] == 'unknown'
    assert chosen(raw, dimensions={'ex:SegmentAxis': 'ex:SegmentMember'})['status'] == 'selected'
    assert chosen(raw.replace(b'xbrldi:explicitMember', b'xbrldi:typedMember'))['status'] == 'unknown'


def test_unit_mismatch_and_unresolved_reference():
    assert chosen(filing(fact()).replace(b'iso4217:USD', b'xbrli:shares'))['status'] == 'unknown'
    assert chosen(filing(fact(context='missing')))['status'] == 'unknown'


def test_duplicate_context_and_truncated_fact_rejected():
    with pytest.raises(ValueError, match='duplicate'):
        parse(filing(fact()) + filing(''))
    with pytest.raises(ValueError, match='unclosed'):
        parse(filing(fact())[:-17])


def test_unsupported_transform_and_scale_limit():
    assert chosen(filing(fact()).replace(b'ixt:num-dot-decimal', b'ixt:unrecognized'))['status'] == 'unknown'
    assert chosen(filing(fact()).replace(b'scale="6"', b'scale="999"'))['status'] == 'unknown'
    assert chosen(filing(fact('—')).replace(b'ixt:num-dot-decimal', b'ixt:zerodash'))['value'] == '0'


def test_nested_facts_are_not_silently_dropped():
    inner = fact('2,539')
    outer = '<ix:nonFraction name="other:Concept" contextRef="q" unitRef="usd">' + inner + '</ix:nonFraction>'
    assert chosen(filing(fact() + outer))['reason'] == 'conflicting_matching_facts'
    assert chosen(filing(outer))['value'] == '2539000000'
