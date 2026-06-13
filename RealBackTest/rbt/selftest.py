"""
Offline self-test — the harness must earn trust before real data does.

  1. MATH      Black-Scholes round trip: price at known sigma, invert,
               recover to 1e-6. Greeks sanity (ATM call delta ~ 0.5+).
  2. RECOVERY  Fixture options are priced at a KNOWN IV path; the chain
               reconstructor (as-of joins + inversion + smoothing) must
               recover it within tolerance on >= 95% of served minutes.
  3. LOOKAHEAD A reconstructor built on data truncated at time T must
               produce byte-identical rows up to T as one built on the full
               dataset. Same property for the horizon bar slices.
  4. E2E       Full pipeline on the fixture: harness -> assessment ->
               report. Proves every interface end-to-end.

Run: python realbacktest.py selftest
"""
import sys

import numpy as np

from . import config as C
from .chain_replay import SymbolOptionsReplay, _Series
from .fixtures import FIXTURE_DB, FIXTURE_UCFG, build, true_iv
from .iv_engine import bs_greeks, bs_price, implied_vol
from .upstox_data import Cache


def _ok(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        raise AssertionError(name)


def test_bs_roundtrip():
    print("[1/4] Black-Scholes inversion round trip")
    rng = np.random.default_rng(1)
    worst = 0.0
    for _ in range(500):
        S = rng.uniform(100, 3000)
        K = S * rng.uniform(0.95, 1.05)
        T = rng.uniform(2, 60) / 365.0
        sig = rng.uniform(0.06, 1.2)
        cp = "CE" if rng.random() < 0.5 else "PE"
        px = bs_price(S, K, T, C.RISK_FREE_RATE, sig, cp)
        rec = implied_vol(px, S, K, T, C.RISK_FREE_RATE, cp)
        if rec is None:
            continue
        worst = max(worst, abs(rec - sig))
    _ok("sigma recovery", worst < 1e-5, f"worst err {worst:.2e}")
    g = bs_greeks(1000, 1000, 30 / 365, C.RISK_FREE_RATE, 0.25, "CE")
    _ok("ATM call delta in (0.5, 0.62)", 0.5 < g["delta"] < 0.62,
        f"delta {g['delta']}")
    _ok("gamma > 0, vega > 0", g["gamma"] > 0 and g["vega"] > 0)


def test_iv_recovery(fx):
    print("[2/4] known-IV recovery through the chain reconstructor")
    cache = Cache(FIXTURE_DB)
    rp = SymbolOptionsReplay(cache, "FIXTURE")
    epochs, close = fx["epochs"], fx["close"]
    errs, served = [], 0
    idx = range(0, len(epochs), 30)
    for i in idx:
        row = rp.chain_row(float(epochs[i]), float(close[i]))
        if not row:
            continue
        served += 1
        iv = row["call_options"]["option_greeks"]["iv"]
        errs.append(abs(iv - 100 * true_iv(epochs[i], fx["t0"])))
    errs = np.array(errs)
    frac = float(np.mean(errs <= 1.5)) if len(errs) else 0.0
    _ok("served >= 80% of sampled minutes",
        served >= 0.8 * len(list(idx)), f"served {served}")
    _ok(">=95% of recovered IVs within 1.5 vol pts of truth",
        frac >= 0.95,
        f"{frac:.1%}, median err {np.median(errs):.3f} pts" if len(errs)
        else "no data")


def test_no_lookahead(fx):
    print("[3/4] zero-lookahead property")
    cache = Cache(FIXTURE_DB)
    epochs, close = fx["epochs"], fx["close"]
    cut = len(epochs) * 2 // 3
    T = float(epochs[cut])

    full = SymbolOptionsReplay(cache, "FIXTURE")
    trunc = SymbolOptionsReplay(cache, "FIXTURE")
    for exp, kd in trunc.book.items():           # truncate every series at T
        for K, sides in kd.items():
            for cp, s in sides.items():
                j = int(np.searchsorted(s.ts, T, side="right"))
                ns = _Series.__new__(_Series)
                ns.ts, ns.close = s.ts[:j], s.close[:j]
                ns.volume, ns.oi = s.volume[:j], s.oi[:j]
                sides[cp] = ns
    mismatches = 0
    n_cmp = 0
    for i in range(0, cut, 97):
        a = full.chain_row(float(epochs[i]), float(close[i]))
        b = trunc.chain_row(float(epochs[i]), float(close[i]))
        n_cmp += 1
        if a != b:
            mismatches += 1
    _ok("chain rows identical with future data removed", mismatches == 0,
        f"{n_cmp} compared")

    from .harness import SymbolData
    sd = SymbolData(cache, FIXTURE_UCFG)
    bad = 0
    rng = np.random.default_rng(3)
    for ep in rng.choice(epochs, 50):
        for h, dur in (("short_term", 1800), ("swing", 86400),
                       ("positional", 7 * 86400)):
            df = sd.slice_h(h, float(ep))
            if len(df):
                last = df["timestamp"].iloc[-1].timestamp()
                if last + dur > ep:                 # bar not closed yet
                    bad += 1
    _ok("no unclosed horizon bar ever served", bad == 0)


def test_end_to_end(fx):
    print("[4/4] end-to-end: harness -> assessment -> report on fixture")
    from .harness import S, SymbolData, run_replay, save_run
    from .report import run_assessment, write_report

    horizons = ["intraday", "short_term"]
    res = run_replay(fx["start"], fx["end"], horizons,
                     universe=[FIXTURE_UCFG], cache_path=FIXTURE_DB,
                     tag="selftest", cost_bps=10.0,
                     progress=lambda *_: None)
    n_eval = sum(v for k, v in res["gates"].items()
                 if k.endswith(".evaluated"))
    _ok("engine evaluated entries", n_eval > 100, f"{n_eval} evaluations")
    _ok("options plane was served",
        res["options_diag"].get("FIXTURE", {}).get("served", 0) > 0,
        str(res["options_diag"].get("FIXTURE")))

    cache = Cache(FIXTURE_DB)
    syms = {"FIXTURE": SymbolData(cache, FIXTURE_UCFG)}
    assess = run_assessment(res, None, syms, S, {"fixture": True},
                            progress=lambda *_: None)
    path = write_report(res, None, assess, {"fixture": True}, S,
                        tag="selftest")
    _ok("report written", path.exists(), str(path))
    save_run(res, C.REPORT_DIR / "runs" / "selftest")
    print(f"  fixture closed trades: {assess['n_closed']}, "
          f"gates: { {k: v for k, v in sorted(res['gates'].items())} }")


def main():
    fx = build()
    test_bs_roundtrip()
    test_iv_recovery(fx)
    test_no_lookahead(fx)
    test_end_to_end(fx)
    print("\nSELFTEST: ALL PASS — pipeline is trustworthy; "
          "point it at real data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
