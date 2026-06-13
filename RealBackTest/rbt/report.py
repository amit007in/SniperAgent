"""
Assessment + realworthiness report.

Consumes the main run, the flow-ablation run, the benchmark results and the
data audit, and writes reports/realworthiness_report_<tag>.md with an
explicit GO / NO-GO verdict against the criteria in config.GO_CRITERIA.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from .benchmarks import (block_bootstrap_p, buy_and_hold, deflated_sharpe,
                         equity_curve_stats, random_entry_null)
from .chain_replay import IST


def _fmt(x, nd=3):
    return "—" if x is None else f"{x:.{nd}f}"


def _md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def per_horizon_stats(trades):
    rows = {}
    for h, g in trades.groupby("horizon"):
        r = g["r_net"].values
        rows[h] = {
            "trades": len(g),
            "hit_rate": float(np.mean(r > 0)) if len(r) else 0.0,
            "mean_R": float(np.mean(r)) if len(r) else 0.0,
            "R_sharpe": (float(np.mean(r) / np.std(r, ddof=1))
                         if len(r) > 2 and np.std(r) > 1e-12 else 0.0),
            "t_stat": (float(np.mean(r) / (np.std(r, ddof=1)
                       / np.sqrt(len(r)))) if len(r) > 2
                       and np.std(r) > 1e-12 else 0.0),
            "pnl_net": float(g["pnl_net"].sum()),
        }
    return rows


def fold_stats(trades, n_folds=4):
    """Walk-forward progression: trades bucketed by entry time into equal
    time folds. Online learning means later folds are increasingly
    out-of-sample relative to what the learners knew earlier."""
    if trades.empty:
        return []
    t = trades.copy()
    t["et"] = pd.to_datetime(t["entry_time"], format="ISO8601", utc=False)
    lo, hi = t["et"].min(), t["et"].max()
    if lo == hi:
        return []
    edges = pd.date_range(lo, hi, periods=n_folds + 1)
    out = []
    for i in range(n_folds):
        g = t[(t["et"] >= edges[i]) & (t["et"] <= edges[i + 1])] \
            if i == n_folds - 1 else \
            t[(t["et"] >= edges[i]) & (t["et"] < edges[i + 1])]
        r = g["r_net"].values
        out.append({
            "fold": i + 1,
            "from": str(edges[i].date()), "to": str(edges[i + 1].date()),
            "trades": len(g),
            "mean_R": float(np.mean(r)) if len(r) else None,
            "hit": float(np.mean(r > 0)) if len(r) else None,
            "pnl_net": float(g["pnl_net"].sum()) if len(g) else 0.0})
    return out


def learning_split_test(trades):
    """First-half vs second-half mean net R (by entry order). The learning
    loop should not be degrading performance over time."""
    if len(trades) < 10:
        return None
    t = trades.sort_values("entry_time")["r_net"].values
    h = len(t) // 2
    a, b = t[:h], t[h:]
    return {"first_half_mean_R": float(np.mean(a)),
            "second_half_mean_R": float(np.mean(b)),
            "improved": bool(np.mean(b) >= np.mean(a))}


def run_assessment(main_res, abl_res, syms, S, audit_data, n_trials=10,
                   progress=print):
    """All numbers the report needs, in one dict."""
    trades = main_res["trades"]
    closed = trades[trades["outcome"] != "OPEN_EOT"]
    r_all = closed["r_net"].values

    start_ep = datetime.fromisoformat(main_res["start"]).replace(
        tzinfo=IST).timestamp()
    end_ep = datetime.fromisoformat(main_res["end"]).replace(
        tzinfo=IST).timestamp() + 86400

    # random-entry null per (horizon, symbol) with matched trade counts
    progress("[ASSESS] random-entry null distributions ...")
    nulls = {}
    for (h, sym), g in closed.groupby(["horizon", "symbol"]):
        if len(g) < 5 or sym not in syms:
            continue
        draws = random_entry_null(syms[sym], S, h, len(g), start_ep, end_ep,
                                  main_res["cost_bps"])
        if len(draws):
            strat = float(g["r_net"].mean())
            pct = float(np.mean(draws < strat) * 100.0)
            nulls[f"{h}/{sym}"] = {
                "strategy_mean_R": strat, "null_mean": float(draws.mean()),
                "null_p95": float(np.percentile(draws, 95)),
                "strategy_pctile": pct, "n_trades": len(g)}

    progress("[ASSESS] bootstrap + deflated Sharpe ...")
    boot_p = block_bootstrap_p(r_all) if len(r_all) >= 5 else None
    sr, dsr = deflated_sharpe(r_all, n_trials=n_trials)

    bh = {}
    for sym, sd in syms.items():
        b = buy_and_hold(sd, start_ep, end_ep)
        if b:
            bh[sym] = b

    abl = None
    if abl_res is not None:
        a_closed = abl_res["trades"]
        a_r = a_closed["r_net"].values
        abl = {"trades": len(a_closed),
               "mean_R": float(np.mean(a_r)) if len(a_r) else 0.0,
               "pnl_net": float(a_closed["pnl_net"].sum())
               if len(a_closed) else 0.0}

    return {
        "n_closed": len(closed),
        "overall": {
            "mean_R": float(np.mean(r_all)) if len(r_all) else 0.0,
            "hit_rate": float(np.mean(r_all > 0)) if len(r_all) else 0.0,
            "sr_per_trade": sr, "dsr": dsr, "bootstrap_p": boot_p,
            **equity_curve_stats(closed)},
        "per_horizon": per_horizon_stats(closed),
        "per_symbol": {s: {"trades": int(n), "pnl_net": float(p)}
                       for s, n, p in
                       [(s, len(g), g["pnl_net"].sum())
                        for s, g in closed.groupby("symbol")]},
        "folds": fold_stats(closed),
        "learning_split": learning_split_test(closed),
        "nulls": nulls,
        "buy_hold": bh,
        "ablation": abl,
        "outcomes": closed["outcome"].value_counts().to_dict(),
    }


def go_no_go(assess, main_res, capital):
    """Evaluate config.GO_CRITERIA. Returns (verdict, checklist rows)."""
    cr = C.GO_CRITERIA
    rows = []

    def add(name, ok, detail):
        rows.append((name, "PASS" if ok else ("FAIL" if ok is False
                                              else "N/A"), detail))
        return ok

    o = assess["overall"]
    n = assess["n_closed"]
    checks = []
    checks.append(add(
        "Positive mean net R", o["mean_R"] > 0,
        f"mean R = {o['mean_R']:+.3f} over {n} closed trades"))
    bp = o["bootstrap_p"]
    checks.append(add(
        f"Bootstrap p < {cr['bootstrap_p_max']}",
        None if bp is None else bp < cr["bootstrap_p_max"],
        f"p = {_fmt(bp, 4)}"))
    if assess["nulls"]:
        worst = min(v["strategy_pctile"] for v in assess["nulls"].values())
        med = float(np.median([v["strategy_pctile"]
                               for v in assess["nulls"].values()]))
        checks.append(add(
            f"Beats random entries (median pctile ≥ "
            f"{cr['random_entry_pctile_min']})",
            med >= cr["random_entry_pctile_min"],
            f"median pctile = {med:.1f}, worst cell = {worst:.1f}"))
    else:
        checks.append(add("Beats random entries", None,
                          "too few trades per cell"))
    diag = main_res.get("options_diag", {})
    if diag:
        worst_iv = min(d["iv_success_rate"] for d in diag.values())
        checks.append(add(
            f"IV recovery ≥ {cr['iv_recovery_min']:.0%}",
            worst_iv >= cr["iv_recovery_min"],
            f"worst symbol = {worst_iv:.1%}"))
    dd_ok = o["max_dd_rupees"] <= cr["maxdd_frac_capital_max"] * capital
    checks.append(add(
        f"Max DD ≤ {cr['maxdd_frac_capital_max']:.0%} of capital", dd_ok,
        f"₹{o['max_dd_rupees']:,.0f} vs ₹{capital:,.0f}"))
    dsr = o["dsr"]
    checks.append(add(
        "Deflated Sharpe > 0.5 (prob true SR > 0)",
        None if dsr is None else dsr > 0.5, f"DSR = {_fmt(dsr, 3)}"))
    ls = assess["learning_split"]
    checks.append(add(
        "Learning not degrading (2nd half ≥ 1st half − 0.1R)",
        None if ls is None else
        ls["second_half_mean_R"] >= ls["first_half_mean_R"] - 0.10,
        "—" if ls is None else
        f"{ls['first_half_mean_R']:+.3f} → {ls['second_half_mean_R']:+.3f}"))
    if assess["ablation"] is not None and n >= 10:
        abl_mean = assess["ablation"]["mean_R"]
        checks.append(add(
            "Edge survives flow-proxy ablation (mean R > 0 with x6 = 0)",
            abl_mean > 0,
            f"ablated mean R = {abl_mean:+.3f} vs main {o['mean_R']:+.3f}"))
    checks.append(add(
        f"Sample size ≥ {cr['min_trades_per_claim']} closed trades",
        n >= cr["min_trades_per_claim"], f"n = {n}"))

    hard = [c for c in checks if c is not None]
    verdict = ("GO — promote to forward paper-trading"
               if all(hard) and len(hard) >= 5 else
               "NO-GO — see failed checks" if any(c is False for c in checks)
               else "INCONCLUSIVE — insufficient evidence")
    return verdict, rows


def write_report(main_res, abl_res, assess, audit_data, S, tag="main"):
    C.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = C.REPORT_DIR / f"realworthiness_report_{tag}.md"
    o = assess["overall"]
    verdict, checklist = go_no_go(assess, main_res, S.CAPITAL)
    L = []
    L.append(f"# Real-Data Worthiness Report — `{tag}`")
    L.append(f"_Generated {datetime.now(timezone.utc).isoformat()}Z · "
             f"window **{main_res['start']} → {main_res['end']}** · "
             f"costs **{main_res['cost_bps']} bps round-trip** · horizons "
             f"{', '.join(main_res['horizons'])} · symbols "
             f"{', '.join(main_res['symbols'])}_")
    L.append("")
    L.append(f"## VERDICT: **{verdict}**")
    L.append("")
    L.append(_md_table(("check", "result", "detail"), checklist))

    L.append("\n## 1. Headline (closed trades, net of costs)")
    L.append(_md_table(
        ("metric", "value"),
        [("closed trades", assess["n_closed"]),
         ("hit rate", f"{o['hit_rate']:.1%}"),
         ("mean net R", f"{o['mean_R']:+.3f}"),
         ("per-trade Sharpe", _fmt(o["sr_per_trade"])),
         ("deflated Sharpe (prob)", _fmt(o["dsr"])),
         ("bootstrap p (mean R > 0)", _fmt(o["bootstrap_p"], 4)),
         ("total net PnL", f"₹{o['total_pnl_net']:,.0f}"),
         ("max drawdown", f"₹{o['max_dd_rupees']:,.0f}"),
         ("outcomes", json.dumps(assess["outcomes"])),
         ("open at end (excluded)", len(main_res["open_left"]))]))

    L.append("\n## 2. Per-horizon")
    L.append(_md_table(
        ("horizon", "trades", "hit", "mean R", "t-stat", "net PnL ₹",
         "p* end", "calib a/b"),
        [(h, v["trades"], f"{v['hit_rate']:.1%}", f"{v['mean_R']:+.3f}",
          f"{v['t_stat']:+.2f}", f"{v['pnl_net']:,.0f}",
          f"{main_res['p_star'].get(h, float('nan')):.3f}",
          f"{main_res['calib'][h]['a']:.2f}/"
          f"{main_res['calib'][h]['b']:+.2f}")
         for h, v in assess["per_horizon"].items()]))

    L.append("\n## 3. Strategy vs random-entry null (same exits, same costs)")
    if assess["nulls"]:
        L.append(_md_table(
            ("cell", "n", "strategy mean R", "null mean", "null p95",
             "strategy pctile"),
            [(k, v["n_trades"], f"{v['strategy_mean_R']:+.3f}",
              f"{v['null_mean']:+.3f}", f"{v['null_p95']:+.3f}",
              f"{v['strategy_pctile']:.1f}")
             for k, v in assess["nulls"].items()]))
        L.append("\n_Read: pctile is where the strategy's mean R lands in "
                 "200 random-entry portfolios of the same size. > 95 means "
                 "the evidence gating beats luck._")
    else:
        L.append("_Too few trades per (horizon, symbol) cell._")

    L.append("\n## 4. Buy-and-hold reference")
    L.append(_md_table(
        ("symbol", "total return", "ann vol", "Sharpe", "max DD"),
        [(s, f"{b['total_return']:+.1%}", f"{b['ann_vol']:.1%}",
          f"{b['sharpe']:+.2f}", f"{b['max_dd_pct']:.1%}")
         for s, b in assess["buy_hold"].items()]))
    L.append("\n_The agent risks a few % of one sleeve per trade — compare "
             "risk-adjusted, not absolute, returns._")

    L.append("\n## 5. Walk-forward progression (online learning)")
    if assess["folds"]:
        L.append(_md_table(
            ("fold", "window", "trades", "mean R", "hit", "net PnL ₹"),
            [(f["fold"], f"{f['from']}→{f['to']}", f["trades"],
              _fmt(f["mean_R"]), "—" if f["hit"] is None
              else f"{f['hit']:.0%}", f"{f['pnl_net']:,.0f}")
             for f in assess["folds"]]))
    ls = assess["learning_split"]
    if ls:
        L.append(f"\nFirst half mean R **{ls['first_half_mean_R']:+.3f}** → "
                 f"second half **{ls['second_half_mean_R']:+.3f}** "
                 f"({'improving' if ls['improved'] else 'degrading'}).")

    L.append("\n## 6. Flow-proxy ablation (x6 = 0)")
    if assess["ablation"]:
        a = assess["ablation"]
        L.append(_md_table(
            ("run", "trades", "mean R", "net PnL ₹"),
            [("main (BVC flow proxy)", assess["n_closed"],
              f"{o['mean_R']:+.3f}", f"{o['total_pnl_net']:,.0f}"),
             ("ablated (flow zeroed)", a["trades"], f"{a['mean_R']:+.3f}",
              f"{a['pnl_net']:,.0f}")]))
        L.append("\n_x6 is the only reconstructed feature without a direct "
                 "historical counterpart (BVC executed-flow proxy for "
                 "live depth imbalance). If the edge dies when it is "
                 "zeroed, the proxy — not the market — was the edge._")

    L.append("\n## 7. Options-plane reconstruction quality")
    diag = main_res.get("options_diag", {})
    if diag:
        L.append(_md_table(
            ("symbol", "chain calls", "served", "availability",
             "IV success", "parity rescues", "stale", "no contract"),
            [(s, d["calls"], d["served"], f"{d['availability']:.1%}",
              f"{d['iv_success_rate']:.1%}", d["iv_parity_rescues"],
              d["stale"], d["no_contract"]) for s, d in diag.items()]))
        L.append("\n_IV recovered by Black-Scholes inversion of real ATM "
                 "premiums; PCR from real per-minute OI; flow via BVC. "
                 "When availability gaps occur the engine degrades to "
                 "structure-only with the P_STAR_NO_OPTIONS floor — the "
                 "identical live failure path._")
    else:
        L.append("_No options plane this run (structure-only)._ ")

    L.append("\n## 8. Gate telemetry (why it didn't trade)")
    g = main_res["gates"]
    if g:
        hz = sorted({k.split(".")[0] for k in g})
        gates = sorted({k.split(".", 1)[1] for k in g})
        L.append(_md_table(
            ["gate"] + hz,
            [[gt] + [g.get(f"{h}.{gt}", 0) for h in hz] for gt in gates]))

    L.append("\n## 9. Learning evolution (Δ weight, earned credit)")
    for h in main_res["horizons"]:
        rows = [(k.split(".")[1], f"{main_res['dw'][k]:+.3f}",
                 f"{main_res['credit'][k]:+.3f}")
                for k in main_res["dw"] if k.startswith(h + ".")]
        if rows:
            L.append(f"\n**{h}**")
            L.append(_md_table(("feature", "Δw", "credit"), rows))

    L.append("\n## 10. Data audit")
    L.append("```json\n" + json.dumps(audit_data, indent=2, default=str)
             + "\n```")

    L.append("\n## Method notes")
    L.append(
        "- Engine under test: `SmartAgent/allstrategy.py`, unmodified — "
        "clock, bar feed and chain feed patched; fusion, calibration, "
        "gates, Kelly, exits and learning untouched.\n"
        "- No lookahead: 30-min bars visible only after close, daily bars "
        "next day, weekly bars next week; options joined backward as-of "
        f"with a {C.OPT_STALENESS_S // 60}-min staleness cap.\n"
        "- IV by Brent inversion of Black-Scholes on real premiums "
        f"(r = {C.RISK_FREE_RATE:.1%}); put-call-parity fallback; EWMA "
        "smoothing with jump filter.\n"
        "- Flow x6 via Bulk Volume Classification (Easley, López de Prado, "
        "O'Hara 2012) over the ATM CE; honesty enforced by the ablation "
        "run.\n"
        "- Null model: random entries on the engine's own evaluation grid "
        "with the identical triple-barrier exit walk and costs.\n"
        "- DSR per Bailey & López de Prado (2014).")

    path.write_text("\n".join(L))
    return path
