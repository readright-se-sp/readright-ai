import re
from itertools import accumulate

_QUOTES = str.maketrans({"’": "'", "‘": "'", "`": "'"})
_SPLIT = re.compile(r"[\s\-–—/]+")
_NOT_WORD = re.compile(r"[^a-z0-9]")

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n: int) -> list[str]:
    """Spells 0-999 as words ('21' -> twenty one). Larger numbers stay digits."""
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        tens, ones = divmod(n, 10)
        return [_TENS[tens]] + ([_ONES[ones]] if ones else [])
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return [_ONES[hundreds], "hundred"] + (number_words(rest) if rest else [])
    return [str(n)]


def normalize(raw: str) -> list[str]:
    """Lowercase, drop punctuation + apostrophes, split hyphens, spell numbers. Same rule both sides."""
    out: list[str] = []
    for part in _SPLIT.split(raw.translate(_QUOTES).lower()):
        word = _NOT_WORD.sub("", part)
        if not word:
            continue
        out.extend(number_words(int(word)) if word.isdigit() else [word])
    return out


def tokenize_passage(text: str) -> list[str]:
    """Passage text -> normalised word list (the reference NW aligns against)."""
    return [w for raw in text.split() for w in normalize(raw)]


def levenshtein(a: str, b: str) -> int:
    """Character edit distance (insert / delete / substitute)."""
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    row = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, row[0] = row[0], i
        for j in range(1, len(b) + 1):
            cur = row[j]
            row[j] = min(row[j - 1] + 1, row[j] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return row[-1]


def damerau(a: str, b: str) -> int:
    """Levenshtein + swapping two neighbouring letters as ONE edit (optimal string alignment).

    from/form = 1 (Levenshtein says 2), girls/gril = 2 (Levenshtein says 3).
    """
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            row[j] = min(row[j - 1] + 1, prev[j] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], prev2[j - 2] + 1)
        prev2, prev = prev, row
    return prev[-1]


def word_distance(a: str, b: str) -> float:
    """0.0 identical .. 1.0 nothing in common (Damerau edit distance / longer word)."""
    if a == b:
        return 0.0
    return min(1.0, damerau(a, b) / max(len(a), len(b)))


_HUM = re.compile(r"(?:[hu]{0,2}m+|mh?m+)")


def is_hum(word: str) -> bool:
    """A humming interjection: m, mm, mmm, hm, hmm, um, umm, uhm, hum, mhm. Any of them reads 'Mmm'."""
    return _HUM.fullmatch(word) is not None


_SOUND_RULES = [
    (re.compile(r"ph"), "f"),
    (re.compile(r"ck|q"), "k"),
    (re.compile(r"c(?=[eiy])"), "s"),
    (re.compile(r"c"), "k"),
    (re.compile(r"x"), "ks"),
    (re.compile(r"z"), "s"),
    (re.compile(r"(?:ie|ey|y)$"), "i"),
    (re.compile(r"y(?=[^aeiou])"), "i"),
    (re.compile(r"(.)\1+"), r"\1"),
]


def sound_key(word: str) -> str:
    """Spelling-insensitive key: words that sound the same get the same key.

    'jacky', 'jackie', 'jaky' -> 'jaki'; 'jack' -> 'jak' (a different sound, so a real misreading).
    """
    for pattern, repl in _SOUND_RULES:
        word = pattern.sub(repl, word)
    return word


def is_proper_noun(word: str, names: frozenset[str]) -> bool:
    """`word` is one of the names, or a name's possessive ("Tinoy's" -> 'tinoys' for name 'tinoy')."""
    return word in names or (word.endswith("s") and word[:-1] in names)


_SENTENCE_END_RAW = re.compile(r"[.!?][\"'”’)\]]*$")


def guess_proper_nouns(text: str) -> list[str]:
    """Names in a passage when none are given: capitalised words that don't start a sentence.

    'But Jacky did not', 'with his father, Mang Ador' -> jacky, mang, ador. Over-guessing ('Mom',
    'Saturday') is harmless: a name only forgives a same-sounding spelling of it (sound_key).
    """
    names: list[str] = []
    starts_sentence = True
    for raw in text.split():
        letters = re.sub(r"[^A-Za-z]", "", raw)
        starts_sentence = starts_sentence or raw[0] in "\"“‘"  # an opening quote starts a quoted sentence
        if len(letters) > 1 and letters[0].isupper() and not starts_sentence:
            names.extend(normalize(raw))
        starts_sentence = _SENTENCE_END_RAW.search(raw) is not None
    return names


def is_anagram(a: str, b: str) -> bool:
    """Same letters, different order (was/saw, on/no, from/form)."""
    return a != b and len(a) > 1 and sorted(a) == sorted(b)


def is_reversal(expected: str, heard: str) -> bool:
    """Phil-IRI reversal: the word read backwards, flipped/mirrored (was/saw, pot/top, on/no)."""
    return expected != heard and len(expected) > 1 and heard == expected[::-1]


def is_letter_transposition(expected: str, heard: str, close_below: float = 0.5) -> bool:
    """Letters out of order, not a full flip: from/form, animal/aminal, and with other slips girls/gril.

    Same letters rearranged, or a close word whose best spelling path swaps two neighbouring letters.
    """
    if is_reversal(expected, heard):
        return False
    if is_anagram(expected, heard):
        return True
    return damerau(expected, heard) < levenshtein(expected, heard) and word_distance(expected, heard) < close_below


def cut_like(heard: str, parts: list[str]) -> list[str]:
    """Cuts `heard` where the boundaries of "".join(parts) fall when the two are lined up letter
    by letter (edit-distance alignment): 'montenoys' vs mang|tinoys -> ['mon', 'tenoys'].
    """
    joined = "".join(parts)
    n, m = len(joined), len(heard)
    dist = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dist[i][0] = i
    for j in range(m + 1):
        dist[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dist[i][j] = min(
                dist[i - 1][j - 1] + (joined[i - 1] != heard[j - 1]), dist[i - 1][j] + 1, dist[i][j - 1] + 1
            )
    at = [0] * (n + 1)  # at[i] = heard letters lined up with joined[:i]
    i, j = n, m
    while i > 0:
        at[i] = j
        if j > 0 and dist[i][j] == dist[i - 1][j - 1] + (joined[i - 1] != heard[j - 1]):
            i, j = i - 1, j - 1
        elif dist[i][j] == dist[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    cuts = [0, *(at[b] for b in accumulate(len(p) for p in parts[:-1])), m]
    return [heard[a:b] for a, b in zip(cuts, cuts[1:])]
