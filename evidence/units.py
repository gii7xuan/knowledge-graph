"""Exact source units and presentation metadata; classification is not entailment."""
from copy import deepcopy
import re

VERSION = 'source_units_v1'


def classify_quote(quote):
    text = str(quote or '').strip()
    if not text or re.fullmatch(r'(?:\$\$|!|\\\[|\\\])', text):
        return {'block_type': 'non_substantive', 'substantive': False, 'requires_image': False}
    if re.fullmatch(r'!?\[[^\]]*\]\([^\n]+\)|https?://\S+', text, re.I):
        is_image=text.startswith('!') or bool(re.search(r'\.(?:png|jpg|jpeg|gif|svg|webp)(?:\?\S*)?$',text,re.I))
        return {'block_type': 'image' if is_image else 'link', 'substantive': False, 'requires_image': is_image}
    kind = ('equation' if (text.startswith('$$') and text.endswith('$$') and len(text) > 4) or
            (text.startswith(r'\[') and text.endswith(r'\]') and len(text) > 4) else
            'table' if text.startswith('|') else 'caption' if re.match(r'^(?:图|表|Fig\.?|Table)\s*\d', text, re.I) else
            'symbol_definition' if re.match(r'^(?:式中|其中|这里|where\b)', text, re.I) else 'paragraph')
    return {'block_type': kind, 'substantive': True, 'requires_image': False}


def source_blocks(index):
    """Parse without changing frozen sentence/index hashes or the source text."""
    text = index['source_snapshot']['canonical_text']
    from document.index_builder import parse_structure
    _, segments = parse_structure(text, index['document_id'])
    blocks = []
    for left, right, section in segments:
        pos = left
        while pos < right:
            if text[pos].isspace():
                pos += 1; continue
            start = pos
            delimiter = '$$' if text.startswith('$$', pos) else r'\[' if text.startswith(r'\[', pos) else None
            if delimiter:
                closer = '$$' if delimiter == '$$' else r'\]'
                stop = text.find(closer, pos + 2, right)
                end = stop + 2 if stop >= 0 else min(right, text.find('\n', pos) + 1 or right)
            else:
                end = text.find('\n', pos, right)
                if end < 0: end = right
                if text[pos:end].lstrip().startswith('|'):
                    while end < right and text[end+1:right].lstrip(' \t').startswith('|'):
                        nxt = text.find('\n', end + 1, right); end = right if nxt < 0 else nxt
            while end > start and text[end-1].isspace(): end -= 1
            if end <= start: end = min(right, start+1)
            quote = text[start:end]
            blocks.append({'document_start': start, 'document_end': end, 'text': quote,
                           'section_id': section['section_id'], **classify_quote(quote)})
            pos = end
    return blocks


def enrich_bundle(bundle, index, candidate):
    """Add complete units as NEW anchored records; never rewrite an existing ID."""
    from .verification import make_evidence_bundle, verify_evidence_bundle
    from .hierarchical_retriever import document_sentence_evidence
    if index.get('schema_version') != 'document_index_v3': return bundle
    blocks = source_blocks(index)
    items = deepcopy(bundle.get('items') or [])
    selected = []
    for item in items:
        overlaps = [i for i,b in enumerate(blocks) if b['section_id'] == item.get('section_id')
                    and b['document_start'] < item['end'] and b['document_end'] > item['start']]
        for i in overlaps:
            selected.append(i)
            if blocks[i]['block_type'] in {'equation', 'symbol_definition', 'caption', 'image'}:
                selected.extend(j for j in range(max(0,i-1),min(len(blocks),i+3)) if blocks[j]['section_id']==blocks[i]['section_id'])
    for i in sorted(set(selected)):
        block = blocks[i]
        item = document_sentence_evidence(index, {**block, 'sentence_id': None}, 'source_block')
        items.append(item)
    known = {}
    for item in items:
        known.setdefault(item['evidence_id'],item)
    for item in known.values():
        item.update(classify_quote(item.get('quote')), evidence_unit_version=VERSION, review_role='unassessed',
                    scope_id=item.get('section_id'), source_span={'start':item['start'],'end':item['end']})
        if item['substantive'] and any(b['block_type']=='equation' and b['document_start']<=item['start']<item['end']<=b['document_end'] and
                (b['document_start'],b['document_end'])!=(item['start'],item['end']) for b in blocks):
            item.update(block_type='equation_fragment',substantive=False,requires_context=True)
        item['linked_evidence_ids'] = sorted({other['evidence_id'] for other in known.values()
            if other['evidence_id'] != item['evidence_id'] and other.get('section_id') == item.get('section_id') and
            (other['start'] <= item['end'] and other['end'] >= item['start'] or
             abs(other['start']-item['end']) < 3 or abs(item['start']-other['end']) < 3)})
    return verify_evidence_bundle(make_evidence_bundle(known.values(), bundle.get('searched_levels', [])), index, candidate)


def can_support(item):
    return item.get('anchor_valid') is True and item.get('substantive') is not False and not item.get('requires_context') and classify_quote(item.get('quote') or item.get('text'))['substantive']
