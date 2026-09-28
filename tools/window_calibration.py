"""Score the channel window-views estimator (sf.metrics) against YouTube Studio exports.

    python tools/window_calibration.py                      # every bundle under calib/
    python tools/window_calibration.py calib/VMT-2026-09-16 # one bundle

A bundle folder holds a Studio "Advanced mode -> Content -> Last 365 days" export (`Table data.csv`,
`Totals.csv`, `Chart data.csv`) plus `uploads.json` = the channel's uploads exactly as a site
lookup fetches them from the Data API (built by the bundle builder; `truth.json` is derived).

For each channel it prints, against the Studio daily totals (the truth):
  • the pure model on the whole fetched catalogue (what a first lookup shows),
  • the same with the lookup's CATALOG_LIMIT + back-catalogue term, and the newest-60 sample only,
  • per-video checks: the charted uploads' real last-28/91/182-day views, and the old uploads'
    last-year views by age and format (short vs long),
  • the snapshot-anchored blend after 7..91 simulated days of daily snapshots.
Use it to judge a shape change before shipping it.
"""
import csv
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sf import metrics, search  # noqa: E402

WINS = (("28d", 28), ("3mo", 91), ("6mo", 182), ("1yr", 365))


def load_bundle(folder):
    b = json.load(open(os.path.join(folder, "uploads.json"), encoding="utf-8"))
    t = json.load(open(os.path.join(folder, "truth.json"), encoding="utf-8"))
    now = datetime.fromisoformat(t["now"])
    ch, ups = b["channel"], b["uploads"]
    for v in ups:
        try:
            v["age_days"] = (now - datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))).total_seconds() / 86400
        except Exception:
            v["age_days"] = None
    chart = {}
    cpath = os.path.join(folder, "Chart data.csv")
    if os.path.exists(cpath):
        with open(cpath, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                vid = (r["Content"] or "").strip()
                if "Views" not in r:   # newer exports chart engaged views only: not the same metric
                    continue
                chart.setdefault(vid, []).append((r["Date"], int(r["Views"])))
    daily = [v for _, v in sorted(t.get("daily") or [])]
    return {"name": os.path.basename(folder.rstrip("/\\")), "channel": ch, "uploads": ups, "truth": t["windows"],
            "daily": daily, "table": t.get("table") or {}, "chart": chart, "now": now}


def pct(a, b):
    return 100 * (a / b - 1) if b else float("nan")


def score(bundle, verbose=True):
    ch, ups, W, now = bundle["channel"], bundle["uploads"], bundle["truth"], bundle["now"]
    pub = [v for v in ups if v.get("privacy") == "public" and (v.get("views") or 0) > 0 and v.get("age_days")]
    lines = [f"== {bundle['name']}: {ch['title']} · {ch.get('subscribers'):,} subs · {len(pub)} public uploads · "
             f"{100 * sum(1 for v in pub if v.get('is_short')) / max(1, len(pub)):.0f}% shorts · channel age "
             f"{(now - datetime.fromisoformat(ch['published_at'].replace('Z', '+00:00'))).days} d"]
    created = datetime.fromisoformat(ch["published_at"].replace("Z", "+00:00"))
    age_days = (now - created).days
    ctx = metrics.catalog_context(pub, ch, now)
    lines.append(f"   context: cadence {ctx['cadence']}/wk · plateau x{ctx['mp'][0]:.2f} (long) x{ctx['mp'][1]:.2f} (short) · evergreen x{ctx['me_ch']:.2f}"
                 + (f" · DORMANT ({ctx['days_since_upload']:.0f} d since the last upload)" if ctx.get("dormant") else ""))
    full = metrics.channel_view_windows(pub, WINS, now=now, channel=ch)
    newest = sorted(pub, key=lambda v: v["age_days"])
    capped = metrics.channel_view_windows(newest[:search.CATALOG_LIMIT], WINS, now=now, total_videos=ch.get("videos"),
                                          lifetime_views=ch.get("views"), channel_age_days=age_days, channel=ch)
    s60 = metrics.channel_view_windows(newest[:60], WINS, now=now, total_videos=ch.get("videos"),
                                       lifetime_views=ch.get("views"), channel_age_days=age_days, channel=ch)
    lines.append("   window   studio        model(all)        model(cap %d)      newest-60" % search.CATALOG_LIMIT)
    errs = {}
    for k, d in WINS:
        if k not in W:          # a partial export (e.g. last 28 days only)
            continue
        errs[k] = pct(full[k]["views"], W[k])
        lines.append(f"   {k:5} {W[k]:>11,}   {full[k]['views']:>11,} {errs[k]:+6.1f}%   {capped[k]['views']:>11,} {pct(capped[k]['views'], W[k]):+6.1f}%"
                     f"   {s60[k]['views']:>11,} {pct(s60[k]['views'], W[k]):+6.1f}%")
    if verbose and bundle["chart"]:
        lines.append("   charted uploads, actual/model views in the last 28 / 91 / 182 days:")
        for vid, rows in bundle["chart"].items():
            v = next((x for x in pub if x["video_id"] == vid), None)
            if not v:
                continue
            d = [x for _, x in sorted(rows)]
            act = {w: sum(d[-w:]) for w in (28, 91, 182)}
            mod = {w: round(metrics.video_window_views(v["views"], v["age_days"], w, is_short=int(v.get("is_short") or 0), ctx=ctx)) for w in (28, 91, 182)}
            lines.append(f"     {(v.get('title') or '')[:34]:34} {'S' if v.get('is_short') else 'L'} age {v['age_days']:4.0f}d life {v['views']:>10,}  "
                         + "  ".join(f"{w}d {act[w]:>8,}/{mod[w]:>8,}" for w in (28, 91, 182)))
    if verbose:
        lines.append("   old uploads (age >= 1 yr) with a table row: last-year views, truth vs model, by age x format:")
        for fmt, name in ((0, "long"), (1, "short")):
            for a0, a1 in ((365, 540), (540, 720), (720, 1100), (1100, 4000)):
                sel = [v for v in pub if a0 <= v["age_days"] < a1 and int(v.get("is_short") or 0) == fmt
                       and "views_365" in bundle["table"].get(v["video_id"], {})]
                if not sel:
                    continue
                tr = sum(bundle["table"][v["video_id"]]["views_365"] for v in sel)
                pr = sum(metrics.video_window_views(v["views"], v["age_days"], 365, is_short=int(v.get("is_short") or 0), ctx=ctx) for v in sel)
                fr = statistics.median(bundle["table"][v["video_id"]]["views_365"] / v["views"] for v in sel if v["views"])
                lines.append(f"     {name:5} age {a0:4}-{a1:4}d n={len(sel):3}  truth {tr:>11,}  model {round(pr):>11,} {pct(pr, tr):+7.1f}%   median frac {fr:.3f}")
    if verbose and any("views_28" in r for r in bundle["table"].values()):
        lines.append("   per-format last-28-day views, truth vs model (28-day export):")
        for fmt, name in ((0, "long"), (1, "short")):
            sel = [v for v in pub if int(v.get("is_short") or 0) == fmt and v["video_id"] in bundle["table"]]
            tr = sum(bundle["table"][v["video_id"]]["views_28"] for v in sel)
            pr = sum(metrics.video_window_views(v["views"], v["age_days"], 28, is_short=fmt, ctx=ctx) for v in sel)
            lines.append(f"     {name:5} n={len(sel):3}  truth {tr:>11,}  model {round(pr):>11,} {pct(pr, tr):+7.1f}%")
    if verbose and bundle["daily"]:
        lines.append("   snapshot-anchored blend after N days of daily snapshots (simulated from the Studio daily totals):")
        daily = bundle["daily"]
        for span in (7, 14, 28, 91):
            rows = [{"day": (now - timedelta(days=d)).strftime("%Y-%m-%d"), "views": sum(daily[:len(daily) - d])} for d in range(span, -1, -1)]
            m = metrics.measured_from_snapshots(rows, WINS)
            r = metrics.channel_view_windows(pub, WINS, now=now, measured=m, channel=ch)
            lines.append(f"     {span:3} days: " + "   ".join(f"{k} {pct(r[k]['views'], W[k]):+6.1f}% ({r[k]['method'][:5]})" for k, _ in WINS if k in W)
                         + (f"   tail k={r['1yr']['tail_k']}" if r["1yr"].get("tail_k") is not None else ""))
    if verbose:
        print("\n".join(lines))
    return errs


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    folders = args or sorted(os.path.join(ROOT, "calib", f) for f in os.listdir(os.path.join(ROOT, "calib"))
                             if os.path.exists(os.path.join(ROOT, "calib", f, "uploads.json"))
                             and os.path.exists(os.path.join(ROOT, "calib", f, "truth.json")))
    allerr = {}
    for f in folders:
        allerr[os.path.basename(f)] = score(load_bundle(f), verbose="--brief" not in sys.argv)
        print()
    print("SUMMARY (pure model, whole catalogue, % vs Studio):")
    for name, e in allerr.items():
        print(f"   {name:24} " + "  ".join(f"{k} {e[k]:+6.1f}%" for k in e))
    worst = max(abs(x) for e in allerr.values() for x in e.values())
    mean = statistics.mean(abs(x) for e in allerr.values() for x in e.values())
    print(f"   worst {worst:.1f}%   mean {mean:.1f}%")


if __name__ == "__main__":
    main()
