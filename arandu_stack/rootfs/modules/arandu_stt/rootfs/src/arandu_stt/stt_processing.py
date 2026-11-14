"""Shared lexical safety and reference metrics. Never invokes NLU or HA actions."""
import re
import unicodedata


def normalized(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', text))


def action(text):
    words = set(normalized(text).split())
    for value, tokens in [('off', {'desliga', 'desligue', 'desligar', 'apaga', 'apague', 'apagar'}),
                          ('on', {'liga', 'ligue', 'ligar', 'acende', 'acenda', 'acender'})]:
        if words & tokens:
            return value
    return None


def correct(resolver, raw, profile):
    result = resolver.apply(raw, profile)
    final = result['text']
    before, after = normalized(raw).split(), normalized(final).split()
    def critical(words):
        result = []
        for word in words:
            if word in ('nao', 'nunca', 'jamais') or word.isdigit():
                result.append(word)
            elif action(word):
                result.append(action(word))
        return result
    if critical(before) != critical(after):
        # An uncertain lexical rewrite must not change an explicit action or negation.
        result = {**result, 'text': raw, 'changes': [], 'safety_preserved_raw': True}
    return result


def edit_operations(a, b):
    """Return (substitutions, insertions, deletions) summing to Levenshtein distance."""
    rows, cols = len(a), len(b)
    dp = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows + 1):
        dp[i][0] = i
    for j in range(cols + 1):
        dp[0][j] = j
    for i in range(1, rows + 1):
        left = a[i - 1]
        for j in range(1, cols + 1):
            cost = 0 if left == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j - 1] + cost, dp[i - 1][j] + 1, dp[i][j - 1] + 1)
    substitutions = insertions = deletions = 0
    i, j = rows, cols
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            substitutions += 1
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1
    return substitutions, insertions, deletions


def distance(a, b):
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (left != right)))
        previous = current
    return previous[-1]


def evaluate(reference, raw, final, entity_reference=None):
    if not reference or not normalized(reference):
        return {
            'evaluated': False,
            'precision_status': 'Precisão não mensurável',
            'entity_correct': None,
            'action_correct': None,
            'negation_preserved': None,
            'exact_match_raw': None,
            'exact_match_final': None,
            'substitutions': None,
            'insertions': None,
            'deletions': None,
        }
    ref = normalized(reference)
    ref_words = ref.split()
    ref_chars = list(ref)
    result = {
        'evaluated': True,
        'precision_status': 'Avaliado',
        'entity_correct': None,
        'entity_note': 'Não avaliado: exige anotação de entidade; STT não executa NLU',
    }
    for label, text in [('raw', raw), ('final', final)]:
        value = normalized(text)
        value_words = value.split()
        value_chars = list(value)
        sub_w, ins_w, del_w = edit_operations(ref_words, value_words)
        sub_c, ins_c, del_c = edit_operations(ref_chars, value_chars)
        dist_w = sub_w + ins_w + del_w
        dist_c = sub_c + ins_c + del_c
        result['wer_' + label] = dist_w / len(ref_words)
        result['cer_' + label] = dist_c / len(ref_chars)
        result['substitutions_' + label] = sub_w
        result['insertions_' + label] = ins_w
        result['deletions_' + label] = del_w
        result['word_edits_' + label] = {
            'substitutions': sub_w,
            'insertions': ins_w,
            'deletions': del_w,
            'distance': dist_w,
            'ref_words': len(ref_words),
        }
        result['char_edits_' + label] = {
            'substitutions': sub_c,
            'insertions': ins_c,
            'deletions': del_c,
            'distance': dist_c,
            'ref_chars': len(ref_chars),
        }
        result['exact_match_' + label] = value == ref
        result['negation_preserved_' + label] = ('nao' in ref_words) == ('nao' in value_words)
        result['action_correct_' + label] = action(value) == action(ref) if action(ref) else None
    if entity_reference and normalized(entity_reference):
        target = ' ' + normalized(entity_reference) + ' '
        for label, text in [('raw', raw), ('final', final)]:
            result['entity_correct_' + label] = target in (' ' + normalized(text) + ' ')
        result.update(entity_correct=result['entity_correct_final'], entity_note='Comparação literal com entidade anotada; não executa NLU')
    result.update(
        action_correct=result['action_correct_final'],
        negation_preserved=result['negation_preserved_final'],
        substitutions=result['substitutions_final'],
        insertions=result['insertions_final'],
        deletions=result['deletions_final'],
        exact_match=result['exact_match_final'],
    )
    return result
