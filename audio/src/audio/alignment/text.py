import re

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
