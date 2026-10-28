"""Sequence decoding with anchored valid words and capped contextual priors."""
import math
import re
import time
import unicodedata

from .errors import LexicalError
from .normalizer import norm, spans, Span, dynamic_allowed
from .scoring import evidence, confidence, label
from .semantic import compatibility


class Decoder:
    def __init__(self, vocabulary, index, config):
        self.vocabulary = vocabulary
        self.index = index
        self.config = config

    def correct(self, raw_text):
        started = time.perf_counter()
        text = unicodedata.normalize('NFC', raw_text).replace('’', "'")
        items = spans(text)
        if len(items) > self.config['max_tokens']:
            raise LexicalError('text_too_long', 'Transcrição excede o limite lexical')
        # Exact split forms of registered compounds, never across punctuation.
        merged = []
        i = 0
        while i < len(items):
            found = False
            for size in (3, 2):
                chunk = items[i:i+size]
                if len(chunk) != size or any(not text[a.end:b.start].isspace() for a, b in zip(chunk, chunk[1:])):
                    continue
                compound = self.vocabulary.compounds.get(tuple(norm(x.original) for x in chunk))
                # Canonicalize the historical technical compounds, or rescue split OOVs.
                # Preserve already-valid phrases such as "lava e seca".
                if compound and (compound in {'ar-condicionado', 'wi-fi', 'micro-ondas', 'lava-loucas'}
                                 or any(not self.vocabulary.allowed(x.original) for x in chunk)):
                    merged.append((Span(text[chunk[0].start:chunk[-1].end], chunk[0].start, chunk[-1].end), compound))
                    i += size
                    found = True
                    break
            if not found:
                merged.append((items[i], None))
                i += 1
        normalized_ms = (time.perf_counter() - started) * 1000
        all_candidates = []
        bases = []
        for item, compound in merged:
            keys = (compound,) if compound else self.index.lookup(item.original)
            if not self.config['closed_vocabulary'] and not self.vocabulary.allowed(item.original) and not compound:
                keys = (norm(item.original),)
            if not keys:
                raise LexicalError('no_candidates', 'Não há candidatos no vocabulário permitido')
            all_candidates.append(keys)
            base = {key: evidence(self.vocabulary, self.config, item.original, key) for key in keys}
            for key in keys:
                if key not in self.vocabulary.terms and not dynamic_allowed(item.original):
                    base[key].update(score=0.0, orthographic=0.0, phonetic=0.0, edit=0.0,
                                     sources=['open_vocabulary'], exact=False)
            bases.append(base)
        candidates_ms = (time.perf_counter() - started) * 1000 - normalized_ms
        weights = self.config['score_weights']
        clause_starts = []
        clause_start = 0
        for position in range(len(merged)):
            if position and re.search(r'[.!?;]', text[merged[position-1][0].end:merged[position][0].start]):
                clause_start = position
            clause_starts.append(clause_start)

        def contextual(position, seq, key):
            seq = seq[clause_starts[position]:]
            nxt = norm(merged[position+1][0].original) if position+1 < len(merged) else None
            if position+1 < len(merged) and re.search(r'[.!?;]', text[merged[position][0].end:merged[position+1][0].start]):
                nxt = None
            bi, tri, ahead = self.vocabulary.context(seq, key, nxt)
            ev = bases[position][key]
            gate = max(0.0, min(1.0, ((ev['orthographic'] + ev['phonetic'])/2 - .5)/.5))
            semantic = compatibility(seq, key)
            bonus = gate * (min(weights['bigram'], .18)*bi + min(weights['trigram'], .20)*tri + min(weights['lookahead'], .10)*ahead + min(weights['semantic'], .12)*semantic)
            return ev['score'] + bonus, {'bigram': bi, 'trigram': tri, 'lookahead': ahead, 'semantic': semantic}

        beams = [(0.0, ())]
        for position, keys in enumerate(all_candidates):
            expanded = []
            for score, seq in beams:
                for key in keys:
                    increment, _ = contextual(position, seq, key)
                    expanded.append((score + increment, seq + (key,)))
            expanded.sort(key=lambda pair: (-pair[0], pair[1]))
            beams = expanded[:self.config['beam_width']]
        chosen_score, chosen_sequence = beams[0]
        token_details = []
        replacements = []
        for position, ((item, compound), key) in enumerate(zip(merged, chosen_sequence)):
            seq = chosen_sequence[:position]
            local_score, context = contextual(position, seq, key)
            options = []
            for candidate in all_candidates[position]:
                candidate_score, candidate_context = contextual(position, seq, candidate)
                global_score = max((score for score, path in beams if path[position] == candidate), default=None)
                options.append({'candidate': self.vocabulary.display(candidate, item.original),
                                'score': round(candidate_score, 6),
                                'sequence_score': round(global_score, 6) if global_score is not None else None,
                                'evidence': {**bases[position][candidate], **candidate_context}})
            options.sort(key=lambda x: (-x['score'], x['candidate']))
            other = next((x for x in options if norm(x['candidate']) != key), None)
            margin = local_score - other['score'] if other else 0.0
            evidence_margin = bases[position][key]['score'] - max((ev['score'] for alternative, ev in bases[position].items() if alternative != key), default=bases[position][key]['score'])
            # Strong context can select a word, but cannot conceal ambiguous spelling evidence.
            conf = confidence(bases[position][key], min(margin, evidence_margin))
            if compound:
                conf = .98
            display = self.vocabulary.display(key, item.original)
            if compound:
                display = self.vocabulary.terms[key].display
            replacements.append(display)
            token_details.append({'original': item.original, 'corrected': display,
                                  'changed': item.original != display,
                                  'forced': not compound and not self.vocabulary.allowed(item.original) and self.config['closed_vocabulary'],
                                  'score': round(local_score, 6), 'margin': round(margin, 6),
                                  'evidence_margin': round(evidence_margin, 6),
                                  'sequence_margin': round(chosen_score - max((s for s, path in beams if path[position] != key), default=chosen_score), 6),
                                  'confidence': round(conf, 6), 'confidence_label': label(conf),
                                  'sources': bases[position][key]['sources'], 'context': context,
                                  'alternatives': options[:4]})
        # Restore multiword local name casing, without expanding aliases or dropping tokens.
        hints = []
        position = 0
        while position < len(chosen_sequence):
            restored = False
            for size in range(min(16, len(chosen_sequence)-position), 0, -1):
                end = position+size
                if clause_starts[end-1] > position:
                    continue
                keys = chosen_sequence[position:end]
                hints.extend(sorted(self.vocabulary.phrase_entities.get(keys, ())))
                surface = self.vocabulary.local_surfaces.get(keys)
                if surface and not restored:
                    for j, replacement in enumerate(surface, position):
                        replacements[j] = replacement
                        token_details[j]['corrected'] = replacement
                        token_details[j]['changed'] = token_details[j]['original'] != replacement
                    restored = True
            position += 1
        out = []
        cursor = 0
        for (item, _), replacement in zip(merged, replacements):
            out.extend((text[cursor:item.start], replacement))
            cursor = item.end
        out.append(text[cursor:])
        final = ''.join(out).strip()
        allowed = all(self.vocabulary.allowed(x.original) for x in spans(final))
        if self.config['closed_vocabulary'] and not allowed:
            raise LexicalError('forbidden_output', 'Decodificador produziu token não permitido')
        confidences = [x['confidence'] for x in token_details]
        overall = math.exp(sum(math.log(max(.000001, x)) for x in confidences)/len(confidences)) if confidences else 0.0
        elapsed = (time.perf_counter() - started)*1000
        return {'text': final, 'correction_ms': round(elapsed, 3),
                'changes': [x for x in token_details if x['changed']], 'tokens': token_details,
                'entity_hints': list(dict.fromkeys(hints))[:20], 'confidence': round(overall, 6),
                'confidence_label': label(overall), 'minimum_token_confidence': min(confidences, default=0.0),
                'low_confidence': any(x['confidence_label'] == 'baixa' for x in token_details),
                'confidence_kind': 'heuristic_lexical', 'confidence_is_calibrated_probability': False,
                'all_tokens_allowed': allowed, 'closed_vocabulary': self.config['closed_vocabulary'],
                'forced_corrections': sum(x['forced'] for x in token_details),
                'timings_ms': {'normalization': round(normalized_ms, 3), 'candidates': round(candidates_ms, 3),
                               'decoding_and_metadata': round(elapsed-normalized_ms-candidates_ms, 3)}}
