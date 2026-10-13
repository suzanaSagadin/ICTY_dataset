"""
03_visualizations.py

Creates plots from the tables saved by 02_statistics.py.

Reads these files from the stats folder:
    labels_overall.csv
    labels_per_case_counts.csv
    labels_per_role_counts.csv

Creates these figures (percentages are computed from the count tables):
    01_overall_labels          overall label distribution
    02_labels_per_case_counts  labels per case, stacked bars (sentence counts)
    03_labels_per_case_percent labels per case, 100% stacked bars
    04_labels_per_role_counts  labels per role, stacked bars (sentence counts)
    05_labels_per_role_percent labels per role, 100% stacked bars

Usage:
    python 03_visualizations.py stats
    python 03_visualizations.py stats --out-dir plots --format pdf --dpi 300

By default the figures are saved as PNG in "<stats folder>/plots".
"""

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # render to files only, no window needed
import matplotlib.pyplot as plt
import pandas as pd

LABEL_ORDER = ["agency", "externalization", "none"]
COLORS = {
    "agency": "#0072B2",           # blue
    "externalization": "#D55E00",  # vermillion
    "none": "#7F7F7F",             # grey
}
FALLBACK_COLORS = ["#009E73", "#CC79A7", "#E69F00", "#56B4E9"]  # unexpected labels

MIN_LABEL_SHARE = 4.0   # segments narrower than this many % of the axis get no text


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"'{path.name}' not found in '{path.parent}'. "
            f"Run 02_statistics.py first (without --no-save)."
        )
    return pd.read_csv(path, index_col=0, encoding="utf-8-sig", keep_default_na=False)


def order_labels(columns) -> list:
    known = [c for c in LABEL_ORDER if c in columns]
    extra = [c for c in columns if c not in LABEL_ORDER]
    return known + extra


def label_colors(labels) -> dict:
    colors, fallback = dict(COLORS), iter(FALLBACK_COLORS)
    for lab in labels:
        colors.setdefault(lab, next(fallback, "#333333"))
    return colors


def load_counts(path: Path) -> pd.DataFrame:
    """Count table without the TOTAL row / Total column, sorted by size."""
    df = read_table(path)
    df = df.drop(index="TOTAL", errors="ignore").drop(columns="Total", errors="ignore")
    df = df[order_labels(df.columns)].astype(int)
    return df.loc[df.sum(axis=1).sort_values(ascending=False).index]


# ---------------------------------------------------------------------------
# plotting
# ---------------------------------------------------------------------------
def save(fig, out_dir: Path, name: str, fmt: str, dpi: int) -> None:
    path = out_dir / f"{name}.{fmt}"
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Saved: {path}")


def plot_overall(overall: pd.DataFrame, out_dir: Path, fmt: str, dpi: int) -> None:
    overall = overall.reindex(order_labels(overall.index))
    colors = label_colors(overall.index)

    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    bars = ax.bar(overall.index, overall["count"],
                  color=[colors[l] for l in overall.index], width=0.6)
    for bar, count, pct in zip(bars, overall["count"], overall["percent"]):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"{count}\n({pct:.1f}%)", ha="center", va="bottom", fontsize=10)

    ax.set_title(f"Overall label distribution (n = {int(overall['count'].sum())} sentences)",
                 loc="left", fontweight="bold")
    ax.set_ylabel("Sentences")
    ax.set_ylim(0, overall["count"].max() * 1.18)
    ax.spines[["top", "right"]].set_visible(False)
    save(fig, out_dir, "01_overall_labels", fmt, dpi)


def plot_stacked(counts: pd.DataFrame, group: str, title: str, name: str,
                 percent: bool, out_dir: Path, fmt: str, dpi: int) -> None:
    labels = list(counts.columns)
    colors = label_colors(labels)
    totals = counts.sum(axis=1)
    data = counts.div(totals, axis=0) * 100 if percent else counts

    ylabels = [f"{idx} (n={totals[idx]})" for idx in data.index]
    fig, ax = plt.subplots(figsize=(10, max(3.2, 0.42 * len(data) + 2)))

    left = pd.Series(0.0, index=data.index)
    axis_max = 100 if percent else totals.max()
    for lab in labels:
        vals = data[lab]
        ax.barh(range(len(data)), vals, left=left, color=colors[lab],
                label=lab, height=0.7)
        for i, (v, l) in enumerate(zip(vals, left)):
            if v / axis_max * 100 >= MIN_LABEL_SHARE:
                text = f"{v:.0f}%" if percent else f"{int(v)}"
                ax.text(l + v / 2, i, text, ha="center", va="center",
                        color="white", fontsize=9, fontweight="bold")
        left = left + vals

    ax.set_yticks(range(len(data)))
    ax.set_yticklabels(ylabels)
    ax.invert_yaxis()  # largest group on top
    ax.set_ylabel("")
    ax.set_xlabel("Share of sentences (%)" if percent else "Sentences")
    if percent:
        ax.set_xlim(0, 100)
    ax.set_title(title, loc="left", fontweight="bold", pad=30)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=len(labels), frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    save(fig, out_dir, name, fmt, dpi)


# ---------------------------------------------------------------------------
def main(stats_dir: str, out_dir: str | None, fmt: str, dpi: int) -> None:
    stats = Path(stats_dir)
    if not stats.is_dir():
        raise FileNotFoundError(f"Stats folder not found: {stats}")
    out = Path(out_dir) if out_dir else stats / "plots"
    out.mkdir(parents=True, exist_ok=True)

    overall = read_table(stats / "labels_overall.csv")
    by_case = load_counts(stats / "labels_per_case_counts.csv")
    by_role = load_counts(stats / "labels_per_role_counts.csv")

    plot_overall(overall, out, fmt, dpi)

    plot_stacked(by_case, "case", "Labels per case (sentence counts)",
                 "02_labels_per_case_counts", False, out, fmt, dpi)
    plot_stacked(by_case, "case", "Labels per case (share of sentences)",
                 "03_labels_per_case_percent", True, out, fmt, dpi)

    plot_stacked(by_role, "role", "Labels per role, all cases pooled (sentence counts)",
                 "04_labels_per_role_counts", False, out, fmt, dpi)
    plot_stacked(by_role, "role", "Labels per role, all cases pooled (share of sentences)",
                 "05_labels_per_role_percent", True, out, fmt, dpi)

    print(f"\nAll figures saved to: {out.resolve()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Visualize the tables from 02_statistics.py.")
    ap.add_argument("stats_dir", help="folder created by 02_statistics.py")
    ap.add_argument("--out-dir", default=None, help="folder for the figures (default: <stats_dir>/plots)")
    ap.add_argument("--format", default="png", choices=["png", "pdf", "svg"])
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()
    main(args.stats_dir, args.out_dir, args.format, args.dpi)
