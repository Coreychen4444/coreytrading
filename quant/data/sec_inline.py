"""Offline, conservative numeric extraction from cached SEC Inline XBRL HTML.

This is a research aid, not an XBRL validator or an accounting mapper. Selectors
use the literal QNames in the filing and exact period/entity/unit/dimensions.
Unsupported representations and conflicting duplicates remain unknown. The
source hash proves bytes, not historical availability. No network or live writes.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from html.parser import HTMLParser
import re


class _Node:
    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def all(self, tag):
        found = [self] if self.tag == tag else []
        for child in self.children:
            if isinstance(child, _Node):
                found.extend(child.all(tag))
        return found

    def text(self):
        if self.tag == 'ix:exclude':
            return ''
        return ''.join(c.text() if isinstance(c, _Node) else c for c in self.children)


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.roots, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        if not self.stack and tag not in {'xbrli:context', 'xbrli:unit', 'ix:nonfraction'}:
            return
        node = _Node(tag, attrs)
        if self.stack:
            self.stack[-1].children.append(node)
        else:
            self.roots.append(node)
        if tag not in {'br', 'hr', 'img', 'input', 'meta', 'link', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1].tag == tag:
            self.stack.pop()
        elif any(n.tag == tag for n in self.stack):
            raise ValueError('malformed captured Inline XBRL subtree')

    def handle_data(self, data):
        if self.stack:
            self.stack[-1].children.append(data)


def _one(node, tag):
    rows = node.all(tag)
    if len(rows) != 1:
        raise ValueError('expected exactly one ' + tag)
    return rows[0].text().strip()


def _context(node):
    identifiers = node.all('xbrli:identifier')
    instant = node.all('xbrli:instant')
    period = ({'instant': _one(node, 'xbrli:instant')} if instant else
              {'start': _one(node, 'xbrli:startdate'), 'end': _one(node, 'xbrli:enddate')})
    if len(identifiers) != 1 or (instant and (node.all('xbrli:startdate') or node.all('xbrli:enddate'))):
        raise ValueError('ambiguous context')
    dims = {}
    for member in node.all('xbrldi:explicitmember'):
        dimension = member.attrs['dimension']
        if dimension in dims:
            raise ValueError('duplicate dimension')
        dims[dimension] = member.text().strip()
    # Other segment/scenario content can encode distinctions we cannot interpret.
    unsupported = bool(node.all('xbrldi:typedmember'))
    for container in node.all('xbrli:segment') + node.all('xbrli:scenario'):
        unsupported |= any(isinstance(c, _Node) and c.tag != 'xbrldi:explicitmember'
                           or isinstance(c, str) and bool(c.strip()) for c in container.children)
    return {'entity': identifiers[0].text().strip(),
            'entity_scheme': identifiers[0].attrs.get('scheme'),
            'period': period, 'dimensions': dims, 'unsupported': unsupported}


def _unit(node):
    if node.all('xbrli:divide'):
        numerator = node.all('xbrli:unitnumerator')
        denominator = node.all('xbrli:unitdenominator')
        if len(numerator) != 1 or len(denominator) != 1:
            raise ValueError('unsupported divided unit')
        return {'numerator': sorted(n.text().strip() for n in numerator[0].all('xbrli:measure')),
                'denominator': sorted(n.text().strip() for n in denominator[0].all('xbrli:measure'))}
    return {'numerator': sorted(n.text().strip() for n in node.all('xbrli:measure')),
            'denominator': []}


def _number(node):
    a = node.attrs
    if a.get('xsi:nil', '').lower() in {'true', '1'}:
        raise ValueError('nil')
    if a.get('continuedat') or node.all('ix:nonfraction')[1:]:
        raise ValueError('continuation_or_nested_fact')
    raw = node.text().strip()
    fmt = a.get('format', '')
    if fmt in {'ixt:zerodash', 'ixt:zero-dash'}:
        if raw not in {'-', '—', '–'}:
            raise ValueError('invalid_zero_dash')
        normalized = '0'
    elif fmt in {'ixt:num-dot-decimal', 'ixt:numdotdecimal'}:
        # Grouping must be conventional; malformed 1,2 cannot silently become 12.
        if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?', raw):
            raise ValueError('unsupported_numeric_lexeme')
        normalized = raw.replace(',', '')
    elif not fmt:
        if not re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?', raw):
            raise ValueError('unsupported_numeric_lexeme')
        normalized = raw
    else:
        raise ValueError('unsupported_transform:' + fmt)
    if a.get('sign', '') not in {'', '-'} or (normalized.startswith('-') and a.get('sign')):
        raise ValueError('invalid_sign')
    if not re.fullmatch(r'-?[0-9]{1,3}', a.get('scale', '0')):
        raise ValueError('invalid_scale')
    scale = int(a.get('scale', '0'))
    if abs(scale) > 30 or len(normalized) > 100:
        raise ValueError('numeric_size_limit')
    with localcontext() as ctx:
        ctx.prec = 140
        value = Decimal(normalized) * Decimal(10) ** scale
        if a.get('sign') == '-':
            value = -value
    return format(value, 'f')


def parse(raw: bytes) -> dict:
    """Parse UTF-8 cached filing bytes; preserve unsupported facts as unknown."""
    if not isinstance(raw, bytes) or len(raw) > 30_000_000:
        raise ValueError('expected cached bytes, maximum 30 MB')
    parser = _Parser()
    parser.feed(raw.decode('utf-8-sig'))
    parser.close()
    if parser.stack:
        raise ValueError('unclosed captured Inline XBRL subtree')
    contexts, units, facts = {}, {}, []
    nodes = []
    for root in parser.roots:
        nodes.extend(root.all('ix:nonfraction') if root.tag == 'ix:nonfraction' else [root])
    for node in nodes:
        if node.tag == 'ix:nonfraction':
            row = {'concept': node.attrs.get('name'), 'context': node.attrs.get('contextref'),
                   'unit': node.attrs.get('unitref'), 'id': node.attrs.get('id'),
                   'decimals': node.attrs.get('decimals'), 'value': None, 'error': None}
            try:
                row['value'] = _number(node)
            except (ValueError, InvalidOperation) as exc:
                row['error'] = str(exc)
            facts.append(row)
        else:
            table, transform = (contexts, _context) if node.tag == 'xbrli:context' else (units, _unit)
            key = node.attrs.get('id')
            if not key or key in table:
                raise ValueError('missing or duplicate context/unit id')
            table[key] = transform(node)
    return {'source_sha256': sha256(raw).hexdigest(), 'contexts': contexts, 'units': units,
            'facts': facts, 'historical_availability_verified': False}


def select(document: dict, *, concept: str, entity: str, entity_scheme: str,
           period: dict, unit: dict, dimensions: dict | None = None) -> dict:
    """Select one exact fact identity; conflicting/unsupported duplicates fail closed.

No taxonomy inference, rounding reconciliation, period aggregation, source date
inference, or accounting validation is performed. Values are decimal strings in
base units; callers explicitly choose any million/share normalization.
"""
    dimensions = {} if dimensions is None else dimensions
    if set(period) not in ({'instant'}, {'start', 'end'}):
        raise ValueError('explicit instant or start/end period required')
    if set(unit) != {'numerator', 'denominator'} or not unit['numerator']:
        raise ValueError('explicit unit measures required')
    result = {'status': 'unknown', 'value': None, 'source_sha256': document['source_sha256'],
              'selector': {'concept': concept, 'entity': entity, 'entity_scheme': entity_scheme,
                           'period': period, 'unit': unit, 'dimensions': dimensions},
              'facts': [], 'reason': 'missing_exact_fact', 'historical_availability_verified': False}
    for row in document['facts']:
        context = document['contexts'].get(row['context'])
        if row['concept'] != concept or not context:
            continue
        if (context['entity'] != entity or context['entity_scheme'] != entity_scheme
                or context['period'] != period or context['dimensions'] != dimensions
                or context['unsupported'] or document['units'].get(row['unit']) != unit):
            continue
        result['facts'].append(dict(row))
    rows = result['facts']
    if rows:
        if any(r['error'] or r['value'] is None for r in rows):
            result['reason'] = 'unsupported_matching_fact'
        elif len({Decimal(r['value']) for r in rows}) != 1:
            result['reason'] = 'conflicting_matching_facts'
        else:
            result.update(status='selected', value=rows[0]['value'], reason=None)
    return result
