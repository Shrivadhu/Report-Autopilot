"""
charts.py
---------
Generates the two charts every client report needs: a weekly trend
line (so the client sees direction, not just two snapshot numbers)
and a per-channel revenue breakdown. Saved as PNGs so reportlab can
embed them directly -- reportlab's own charting is clunky for
anything beyond simple bars, matplotlib is more reliable for this.
"""

import matplotlib
matplotlib.use("Agg")  # no display needed, this runs headless/server-side
import matplotlib.pyplot as plt
import pandas as pd

DEFAULT_BRAND_COLOR = "#f27038"     # used when the caller doesn't pass one
BRAND_COLOR_DARK = "#2b2b2b"


def weekly_trend_chart(df: pd.DataFrame, out_path: str, metric: str = "revenue", brand_color: str = DEFAULT_BRAND_COLOR):
    weekly = df.set_index("date").resample("W")[metric].sum()

    fig, ax = plt.subplots(figsize=(7, 3))
    ax.plot(weekly.index, weekly.values, marker="o", color=brand_color, linewidth=2)
    ax.fill_between(weekly.index, weekly.values, color=brand_color, alpha=0.12)
    ax.set_title(f"Weekly {metric.capitalize()} Trend", fontsize=11, color=BRAND_COLOR_DARK, loc="left")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def channel_breakdown_chart(channel_summaries, out_path: str, metric: str = "revenue", brand_color: str = DEFAULT_BRAND_COLOR):
    names = [c.channel for c in channel_summaries]
    values = [getattr(c, metric) for c in channel_summaries]

    order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    names = [names[i] for i in order]
    values = [values[i] for i in order]

    fig, ax = plt.subplots(figsize=(7, 3))
    bars = ax.barh(names, values, color=brand_color)
    ax.set_title(f"{metric.capitalize()} by Channel (current period)", fontsize=11, color=BRAND_COLOR_DARK, loc="left")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.invert_yaxis()
    ax.tick_params(labelsize=8)
    for bar, val in zip(bars, values):
        ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2,
                 f"  {val:,.0f}", va="center", fontsize=8, color=BRAND_COLOR_DARK)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
