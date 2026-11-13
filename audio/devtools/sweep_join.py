"""Label-free sweep of the join window and the coverage guard, from the cache (no models).

Proxy metric: on these recordings CrisperWhisper verbatim plus Lane A's `match` is nearly always a
correct read, so a Lane A `match` row that Lane B changes is a false overrule.

  FOR      = Lane A `match` rows changed to anything else / Lane A `match` rows Lane B judged
  coverage = rows with phonetic.check == "done" / all rows Lane B tried to judge

Selection rule (plans/phase-B6a.md, fixed before looking), on the SELECTION recordings only:
  1. among configs with coverage >= 85%, take the lowest FOR;
  2. configs within 0.5 percentage points of it: take the highest coverage;
  3. then prefer guard off over on, adaptive overrule off over on, short-word rule 0 before 2
     before 3 (simpler wins).
The HELD-OUT recordings are only reported, never used to choose.

Round 1 and round 2 also compared the fixed-pad window rule; it was removed (it measured no better
than midpoint), so those grids now cover midpoint windows only.

    uv run python devtools/cache_runs.py     # once
    uv run python devtools/sweep_join.py     # writes devtools/out/sweep_join.csv
    uv run python devtools/sweep_join.py --round2   # step 4b grid, sweep_join_round2.csv
"""

import csv
import itertools
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "devtools"))

from replay import Cached, Replayer, load_all, with_overrides  # noqa: E402

OUT = ROOT / "devtools" / "out" / "sweep_join.csv"
OUT_ROUND2 = ROOT / "devtools" / "out" / "sweep_join_round2.csv"

# The split, fixed 2026-11-02 before any sweep (plans/phase-B6a.md), by passage: no passage is
# in both sets.
HELD_OUT_PREFIXES = ("mariancame", "nickisatri")  # marian_came (2 recordings), nick_is_a_tri
COVERAGE_FLOOR = 0.85
FOR_TIE_PP = 0.005  # 0.5 percentage points


def is_held_out(c: Cached) -> bool:
    return c.recording.startswith(HELD_OUT_PREFIXES)


@dataclass
class Tally:
    eligible: int = 0
    done: int = 0
    match_judged: int = 0
    false_overrules: int = 0
    clears: int = 0  # SUB / MISPRON -> match
    errors_lane_a: int = 0
    errors_final: int = 0
    false_rows: list[tuple[str, int]] = field(default_factory=list)

    @property
    def false_overrule_rate(self) -> float:
        return self.false_overrules / self.match_judged if self.match_judged else 0.0

    @property
    def coverage(self) -> float:
        return self.done / self.eligible if self.eligible else 0.0


def grid() -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for guard, adaptive in itertools.product((0.0, 0.34, 0.5, 0.67), (True, False)):
        for reach in (100, 150, 200, 300):
            configs.append(
                {
                    "JOIN_MAX_REACH_MS": reach,
                    "COVERAGE_GUARD_RATIO": guard,
                    "ADAPTIVE_OVERRULE": adaptive,
                }
            )
    return configs


def grid_round2() -> list[dict[str, Any]]:
    """Step 4b (plans/phase-B6a.md, "Plan change"): adaptive overrule on only."""
    windows: list[dict[str, Any]] = [{"JOIN_MAX_REACH_MS": r} for r in (100, 150, 200)]
    return [
        {
            **w,
            "COVERAGE_GUARD_RATIO": guard,
            "SHORT_WORD_MAX_PHONES": short,
            "ADAPTIVE_OVERRULE": True,
        }
        for w in windows
        for guard in (0.5, 0.67, 0.8, 1.0)
        for short in (0, 2, 3)
    ]


def name(cfg: dict[str, Any]) -> str:
    window = f"mid{cfg['JOIN_MAX_REACH_MS']}"
    adaptive = "on" if cfg["ADAPTIVE_OVERRULE"] else "off"
    short = cfg.get("SHORT_WORD_MAX_PHONES", 0)
    suffix = f" short{short}" if "SHORT_WORD_MAX_PHONES" in cfg else ""
    return f"{window} guard{cfg['COVERAGE_GUARD_RATIO']} adaptive={adaptive}{suffix}"


def evaluate(
    replayer: Replayer,
    caches: list[Cached],
    base_rows: dict[str, Any],
    cfg: dict[str, Any],
    base_errors: dict[str, int],
) -> tuple[Tally, Tally]:
    settings = with_overrides(replayer.base, **cfg)
    sel, held = Tally(), Tally()
    for c in caches:
        tally = held if is_held_out(c) else sel
        result = replayer.run(c, settings)
        base = base_rows[c.recording]
        assert len(base) == len(result["words"])
        tally.errors_lane_a += base_errors[c.recording]
        tally.errors_final += result["score"]["error_count"]
        for a, b in zip(base, result["words"], strict=True):
            ph = b.get("phonetic")
            if ph is None:
                continue
            tally.eligible += 1
            if ph["check"] != "done":
                continue
            tally.done += 1
            if a["tag"] == "match":
                tally.match_judged += 1
                if b["tag"] != "match":
                    tally.false_overrules += 1
                    tally.false_rows.append((c.recording, a["passage_indices"][0]))
            elif a["tag"] in ("substitution", "mispronunciation") and b["tag"] == "match":
                tally.clears += 1
    return sel, held


def select(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The plan's selection rule on the selection-set numbers."""
    ok = [r for r in rows if r["sel_coverage"] >= COVERAGE_FLOOR]
    if not ok:
        return None
    best = min(r["sel_for"] for r in ok)
    near = [r for r in ok if r["sel_for"] <= best + FOR_TIE_PP + 1e-12]
    top_cov = max(r["sel_coverage"] for r in near)
    tied = [r for r in near if abs(r["sel_coverage"] - top_cov) < 1e-12]
    return min(
        tied,
        key=lambda r: (
            r["COVERAGE_GUARD_RATIO"] > 0,  # guard off first
            bool(r["ADAPTIVE_OVERRULE"]),  # adaptive off first
            r.get("SHORT_WORD_MAX_PHONES", 0),  # 0 before 2 before 3
            r["name"],
        ),
    )


def main() -> None:
    round2 = "--round2" in sys.argv
    out = OUT_ROUND2 if round2 else OUT
    caches = load_all()
    if not caches:
        sys.exit("No cache: run devtools/cache_runs.py first")
    replayer = Replayer()
    base_rows, base_errors = {}, {}
    for c in caches:
        result = replayer.run(c, phonetic=False)
        base_rows[c.recording] = result["words"]
        base_errors[c.recording] = result["score"]["error_count"]
    sel_names = [c.recording[:10] for c in caches if not is_held_out(c)]
    held_names = [c.recording[:10] for c in caches if is_held_out(c)]
    print(f"selection ({len(sel_names)}): {sel_names}\nheld-out ({len(held_names)}): {held_names}")

    rows: list[dict[str, Any]] = []
    for cfg in grid_round2() if round2 else grid():
        sel, held = evaluate(replayer, caches, base_rows, cfg, base_errors)
        rows.append(
            {
                **cfg,
                "SHORT_WORD_MAX_PHONES": cfg.get("SHORT_WORD_MAX_PHONES", 0),
                "name": name(cfg),
                "sel_for": sel.false_overrule_rate,
                "sel_coverage": sel.coverage,
                "sel_false": sel.false_overrules,
                "sel_match_judged": sel.match_judged,
                "sel_clears": sel.clears,
                "sel_errors_lane_a": sel.errors_lane_a,
                "sel_errors_final": sel.errors_final,
                "held_for": held.false_overrule_rate,
                "held_coverage": held.coverage,
                "held_false": held.false_overrules,
                "held_match_judged": held.match_judged,
                "held_clears": held.clears,
                "held_errors_lane_a": held.errors_lane_a,
                "held_errors_final": held.errors_final,
                "held_false_rows": ";".join(f"{r[:10]}#{i}" for r, i in held.false_rows),
                "sel_false_rows": ";".join(f"{r[:10]}#{i}" for r, i in sel.false_rows),
            }
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    def line(r: dict[str, Any]) -> str:
        return (
            f"{r['name']:<40} sel FOR {r['sel_for']:6.2%} cov {r['sel_coverage']:6.2%} "
            f"clears {r['sel_clears']:>2} err {r['sel_errors_lane_a']}->{r['sel_errors_final']} | "
            f"held FOR {r['held_for']:6.2%} cov {r['held_coverage']:6.2%} "
            f"clears {r['held_clears']:>2} err {r['held_errors_lane_a']}->{r['held_errors_final']}"
        )

    ranked = sorted(
        rows, key=lambda r: (r["sel_coverage"] < COVERAGE_FLOOR, r["sel_for"], -r["sel_coverage"])
    )
    print("\nTop 10 by selection FOR (coverage >= 85% first):")
    for r in ranked[:10]:
        print(" ", line(r))
    chosen = select(rows)
    if chosen is None:
        print("\nNO CONFIG reaches coverage >= 85% on the selection set")
        return
    print("\nCHOSEN (selection rule):")
    print(" ", line(chosen))
    patterns = Counter()
    for token in chosen["held_false_rows"].split(";"):
        if token:
            patterns[token.split("#")[0]] += 1
    print("held-out false overrules by recording:", dict(patterns))
    met = chosen["held_for"] <= 0.02 and chosen["held_coverage"] >= COVERAGE_FLOOR
    print(f"target: held-out FOR <= 2% with coverage >= 85%: {'MET' if met else 'NOT MET'}")


if __name__ == "__main__":
    main()
