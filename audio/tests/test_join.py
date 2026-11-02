import random

import pytest
from pydantic import ValidationError

from audio.lane_b.join import join
from audio.types import JoinResult, PassageToken, PhonemeToken, PreparedPassage, Unit

KW = dict(pad_ms=80, coverage_min_ms=120, coverage_max_gap_ms=300)

# keys: a (2 or 3 phonemes), b (1), c (2)
PASSAGE = PreparedPassage(
    passage_hash="h",
    tokens=tuple(PassageToken(index=i, raw=k, key=k) for i, k in enumerate("abcd")),
    pronunciations={
        "a": (("k", "æ", "t"), ("k", "æ")),
        "b": (("x",),),
        "c": (("s", "i"),),
        "d": (("d", "ɔ"), ("d", "ɔ", "g")),
    },
)


def unit(start, end, idx=0, span=1, src=(0,), key="a"):
    return Unit(
        surface=key if src else "",
        key=key,
        passage_index=idx,
        passage_span=span if idx is not None else 0,
        source_indices=src,
        start_ms=start,
        end_ms=end,
        align_score=1.0,
    )


def untimed(idx=0):
    return Unit(
        surface="a",
        key="a",
        passage_index=idx,
        passage_span=1,
        source_indices=(0,),
        start_ms=None,
        end_ms=None,
        align_score=0.0,
    )


def ph(start, end, sym="k"):
    return PhonemeToken(symbol=sym, start_ms=start, end_ms=end, confidence=0.9)


def run(units, phonemes, **kw):
    return join(units, phonemes, PASSAGE, **{**KW, **kw})


def test_inside_padding_and_outside():
    u = [unit(1000, 2000)]
    inside, padded, outside = ph(1400, 1420), ph(2040, 2060), ph(2150, 2170)
    r = run(u, [inside, padded, outside])
    assert r.unit_phonemes == ((inside, padded),)
    assert r.discarded == (outside,)  # 20 ms run, shorter than the minimum


def test_window_edges_inclusive():
    r = run([unit(1000, 2000)], [ph(2070, 2090)])
    assert len(r.unit_phonemes[0]) == 1
    r = run([unit(1000, 2000)], [ph(2071, 2091)])  # mid 2081
    assert r.unit_phonemes[0] == ()


def test_nearer_edge_beats_nearer_centre():
    a, b = unit(0, 1000), unit(1100, 1200, idx=1, key="b")
    p = ph(1030, 1050)  # mid 1040: edge 40 from a, 60 from b; centre 110 from b, 540 from a
    r = run([a, b], [p])
    assert r.unit_phonemes == ((p,), ())


def test_unpadded_containment_wins():
    a, b = unit(1000, 2000), unit(2050, 3000, idx=1, key="b")
    p = ph(1980, 2000)  # mid 1990: inside a, only in b's padding
    r = run([a, b], [p])
    assert r.unit_phonemes == ((p,), ())


def test_tie_goes_to_lower_index():
    a, b = unit(1000, 1100), unit(1160, 1260, idx=1, key="b")
    p = ph(1120, 1140)  # mid 1130: 30 from both edges, 80 from both centres
    assert run([a, b], [p]).unit_phonemes == ((p,), ())


def test_untimed_unit_gets_nothing():
    r = run([untimed(), unit(5000, 5100, idx=1, key="b")], [ph(0, 20)])
    assert r.unit_phonemes[0] == ()
    assert r.ms_per_phoneme[0] is None


def test_timed_omission_receives_phonemes():
    om = unit(1000, 1500, idx=1, src=(), key="b")
    ps = [ph(1100, 1200), ph(1200, 1300)]
    r = run([om], ps)
    assert r.unit_phonemes == (tuple(ps),)
    assert r.ms_per_phoneme == (None,)


def test_run_length_threshold():
    ok = run([], [ph(5000, 5060), ph(5060, 5120)])
    assert len(ok.coverage_candidates) == 1 and ok.discarded == ()
    c = ok.coverage_candidates[0]
    assert (c.start_ms, c.end_ms) == (5000, 5120)
    short = run([], [ph(5000, 5060), ph(5060, 5119)])
    assert short.coverage_candidates == () and len(short.discarded) == 2


def test_silence_splits_runs():
    a, b, c, d = ph(5000, 5060), ph(5060, 5120), ph(5421, 5481), ph(5481, 5541)
    r = run([], [a, b, c, d])  # gap 301
    assert [x.phonemes for x in r.coverage_candidates] == [(a, b), (c, d)]
    r = run([], [a, b, ph(5420, 5480), ph(5480, 5540)])  # gap 300: one run
    assert len(r.coverage_candidates) == 1
    # each run is judged on its own: short first run is dropped, long second kept
    r = run([], [ph(5000, 5060), c, d])
    assert len(r.coverage_candidates) == 1 and len(r.discarded) == 1


def test_after_passage_index():
    units = [
        unit(3000, 3200, idx=0),
        unit(3500, 3900, idx=1, span=2, key="b"),
        unit(4000, 4100, idx=None),  # insertion: ignored
    ]
    before, after = [ph(0, 150)], [ph(5000, 5200)]
    r = run(units, before + after)
    assert [c.after_passage_index for c in r.coverage_candidates] == [None, 2]


def test_ms_per_phoneme():
    # token a: shortest variant 2; token b: 1 -> expected 3
    matched = unit(1000, 1300, idx=0, span=2, src=(0, 1))
    ins = unit(2000, 2100, idx=None, src=(2,))
    r = run([matched, ins], [])
    assert r.ms_per_phoneme == (100.0, None)


def test_every_phoneme_exactly_once():
    units = [unit(1000, 1500), untimed(1), unit(3000, 3400, idx=2, key="c")]
    ps = [
        ph(1000, 1050),
        ph(1500, 1540),
        ph(1600, 1660),  # run of 60 ms -> discarded
        ph(2000, 2100),
        ph(2100, 2250),  # run -> candidate
        ph(3100, 3150),
        ph(9000, 9010),
    ]
    r = run(units, ps)
    out = [p for g in r.unit_phonemes for p in g]
    out += [p for c in r.coverage_candidates for p in c.phonemes] + list(r.discarded)
    assert sorted(out, key=lambda p: p.start_ms) == sorted(ps, key=lambda p: p.start_ms)
    assert len(r.coverage_candidates) == 1 and len(r.discarded) >= 2


def test_empty_phonemes():
    r = run([unit(1000, 1300, idx=0, span=2, src=(0, 1)), untimed(2)], [])
    assert r.unit_phonemes == ((), ())
    assert r.coverage_candidates == () and r.discarded == ()
    assert r.ms_per_phoneme == (100.0, None)


def test_unsorted_equals_sorted():
    units = [unit(1000, 1500), unit(2000, 2400, idx=1, key="b")]
    ps = [ph(1000 + 50 * i, 1040 + 50 * i) for i in range(40)]
    shuffled = ps[:]
    random.Random(1).shuffle(shuffled)
    assert run(units, ps) == run(units, shuffled)


def test_join_result_rejects_mismatched_lengths():
    with pytest.raises(ValidationError):
        JoinResult(unit_phonemes=((),), coverage_candidates=(), discarded=(), ms_per_phoneme=())


# --- midpoint windows (B6a) ---


def mid(units, phonemes, reach=300, **kw):
    return run(units, phonemes, mode="midpoint", max_reach_ms=reach, **kw)


def owners(r, phonemes):
    """For each phoneme: unit index, or None when it is unassigned."""
    out = []
    for p in phonemes:
        owner = next((i for i, g in enumerate(r.unit_phonemes) if p in g), None)
        out.append(owner)
    return out


A, B = unit(1000, 2000), unit(2400, 3000, idx=1, key="b")  # gap 2000..2400, midpoint 2200


def test_midpoint_splits_the_gap_in_the_middle():
    ps = [
        ph(2090, 2110),
        ph(2190, 2200),
        ph(2210, 2230),
        ph(2290, 2310),
    ]  # mids 2100 2195 2220 2300
    assert owners(mid([A, B], ps), ps) == [0, 0, 1, 1]


def test_midpoint_at_exactly_the_middle_goes_to_the_nearer_centre():
    p = ph(2190, 2210)  # midpoint 2200: both windows reach it at the same edge distance
    assert owners(mid([A, B], [p]), [p]) == [1]  # B centre 2700 is nearer than A centre 1500


def test_cap_leaves_far_phonemes_unassigned():
    ps = [ph(2040, 2060), ph(2150, 2170)]  # 50 and 160 ms past A's edge; B is 350 / 240 ms away
    r = mid([A, B], ps, reach=100)
    assert owners(r, ps) == [0, None]
    assert [p for p in r.discarded] == [ps[1]]  # too short for a candidate


def test_first_and_last_units_reach_outward_by_the_cap():
    ps = [ph(890, 910), ph(780, 790), ph(3090, 3110), ph(3250, 3260)]
    r = mid([A, B], ps, reach=150)  # first reaches back to 850, last forward to 3150
    assert owners(r, ps) == [0, None, 1, None]


def test_touching_units_have_no_gap():
    t1, t2 = unit(1000, 2000), unit(2000, 3000, idx=1, key="b")
    ps = [ph(1990, 2000), ph(2000, 2010), ph(2010, 2020)]  # mids 1995, 2005, 2015
    assert owners(mid([t1, t2], ps), ps) == [0, 1, 1]


def test_overlapping_units_use_the_nearer_centre_tie_order():
    o1, o2 = unit(1000, 2000), unit(1800, 2600, idx=1, key="b")
    p_both, p_only2 = ph(1890, 1910), ph(2290, 2310)
    r = mid([o1, o2], [p_both, p_only2])
    assert owners(r, [p_both, p_only2]) == [
        1,
        1,
    ]  # inside both: o2 centre (2200) is nearer; only o2: o2


def test_untimed_units_get_nothing_and_do_not_shorten_a_gap():
    ps = [ph(2290, 2310)]
    r = mid([A, untimed(1), B], ps, reach=300)
    assert r.unit_phonemes[1] == () and owners(r, ps) == [2]


def test_midpoint_every_phoneme_has_exactly_one_owner():
    units = [unit(1000, 1500), untimed(1), unit(3000, 3400, idx=2, key="c")]
    ps = [ph(900 + 80 * i, 940 + 80 * i) for i in range(45)]
    r = mid(units, ps, reach=200)
    out = [p for g in r.unit_phonemes for p in g]
    out += [p for c in r.coverage_candidates for p in c.phonemes] + list(r.discarded)
    assert sorted(out, key=lambda p: p.start_ms) == ps


def test_pad_mode_is_the_old_rule():
    ps = [ph(2040, 2060), ph(2150, 2170)]
    pad = run([A, B], ps, pad_ms=80)
    assert owners(pad, ps) == [0, None]  # 2060 mid is 60 past A (inside 80); 2160 is 160 past
    assert pad == run([A, B], ps, pad_ms=80, mode="pad")
