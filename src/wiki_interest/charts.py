"""Charts from analysis.json. matplotlib with the Agg backend (no display needed).

Two charts, chosen for what a founder will actually look at:
1. chart_monthly.png  - one panel per language: monthly human views (bars),
   the spike-removed series (line), verdict and trust in the title. Answers
   "what does the trend look like, and is it driven by a few days".
2. chart_share.png    - all languages on one axis as views per million edition
   views. The only fair way to put a 7-billion-view edition next to a
   70-million-view one.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b", "#17becf", "#7f7f7f"]


def _month_ticks(labels: list[str]) -> tuple[list[int], list[str]]:
    idx = [i for i, lab in enumerate(labels) if lab.endswith(("-01", "-04", "-07", "-10"))]
    return idx, [labels[i] for i in idx]


def chart_monthly(full: dict, out_path: Path) -> Path:
    langs = list(full["languages"].items())
    n = max(1, len(langs))
    fig, axes = plt.subplots(n, 1, figsize=(10, 2.6 * n + 0.8), squeeze=False)
    for ax, (lang, d), color in zip(axes[:, 0], langs, COLORS * 3):
        rows = d["monthly"]
        labels = [r["month"] for r in rows]
        x = list(range(len(rows)))
        ax.bar(x, [r["views"] for r in rows], color=color, alpha=0.35, width=0.8, label="monthly views")
        ax.plot(x, [r["despiked"] for r in rows], color=color, lw=2, label="spike days removed")
        if d["partial_history"] and d["first_revision"]:
            ax.axvline(-0.5 + (len(rows) - d["months_used"]), color="grey", ls="--", lw=1)
            ax.text(
                len(rows) - d["months_used"],
                ax.get_ylim()[1] * 0.92,
                f" created {d['first_revision']}",
                fontsize=8,
                color="grey",
            )
        t = d["trust"]
        ax.set_title(
            f"{lang}: {d['article']} — {d['verdict']} ({d['magnitude'] or 'n/a'}), trust {t['score']}/100 ({t['label']})",
            fontsize=10,
            loc="left",
        )
        ticks, tl = _month_ticks(labels)
        ax.set_xticks(ticks)
        ax.set_xticklabels(tl, fontsize=8)
        ax.set_ylabel("views / month", fontsize=8)
        ax.grid(axis="y", alpha=0.3)
        ax.legend(fontsize=7, loc="upper right", frameon=False)
    fig.suptitle(
        f"“{full['label'] or full['topic']}” — human pageviews, {full['window']['start']} to {full['window']['end']}",
        fontsize=11,
    )
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


def chart_share(full: dict, out_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(10, 3.6))
    any_line = False
    for (lang, d), color in zip(full["languages"].items(), COLORS * 3):
        rows = d["monthly"]
        ys = [r["per_million"] for r in rows]
        if all(v is None for v in ys):
            continue
        any_line = True
        x = list(range(len(rows)))
        rel = d.get("relative") or {}
        ax.plot(
            x,
            [v if v is not None else float("nan") for v in ys],
            color=color,
            lw=2,
            marker="o",
            ms=3,
            label=f"{lang}: {d['article']} ({rel.get('verdict', 'n/a')} relative)",
        )
        labels = [r["month"] for r in rows]
    if any_line:
        ticks, tl = _month_ticks(labels)
        ax.set_xticks(ticks)
        ax.set_xticklabels(tl, fontsize=8)
    ax.set_ylabel("views per million edition views", fontsize=9)
    ax.set_title("Share of each edition's attention (comparable across languages)", fontsize=10, loc="left")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


def make_charts(full: dict, out_dir: Path) -> list[str]:
    paths = [chart_monthly(full, out_dir / "chart_monthly.png"), chart_share(full, out_dir / "chart_share.png")]
    return [str(p) for p in paths]
