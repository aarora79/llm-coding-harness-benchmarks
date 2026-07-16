#!/usr/bin/env python3
"""Generate Cost vs Quality scatter plot from benchmark data.

Plots each model's average quality score (from judge-gpt.json) against its
effective cost per 1M input tokens. Self-hosted models use instance cost
divided by throughput; API models use published pricing.

Cost data is maintained in MODEL_COSTS below. Update when adding new models.

Usage:
    python3 generate-cost-quality-chart.py [--output-dir ./reports]

Requires: matplotlib, pandas, numpy
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
except ImportError:
    print("Error: matplotlib, numpy, pandas required.", file=sys.stderr)
    sys.exit(1)


BENCH_DIR = Path(__file__).parent.parent / "swe-benchmark-data" / "mcp-gateway-registry"
SKIP_DIRS = {"repo", "implementations", "reports"}

# Cost per 1M input tokens (effective rate)
# API models: published Bedrock pricing
# Self-hosted: (instance_cost_per_hr / prompt_throughput_tokens_per_sec / 3600) * 1_000_000
# Formula: $/hr / (tok/s * 3600) * 1M = $/M_input_tokens
MODEL_COSTS = {
    "claude-opus-4-8": {
        "cost_per_m_input": 5.00,
        "provider": "bedrock",
        "label": "Claude Opus 4.8",
        "color": "#E04E39",
        "size": 200,
    },
    "kimi-k2-thinking": {
        "cost_per_m_input": 0.70,
        "provider": "bedrock",
        "label": "Kimi K2",
        "color": "#7B7FE8",
        "size": 120,
    },
    "kimi-k2-5": {
        "cost_per_m_input": 0.70,
        "provider": "bedrock",
        "label": "Kimi K2.5",
        "color": "#7B7FE8",
        "size": 120,
    },
    "kimi-k2-7-code": {
        "cost_per_m_input": 1.69,
        "provider": "self-hosted",
        "label": "Kimi-K2.7-Code (self-hosted)",
        "color": "#4169E1",
        "size": 160,
        # 55 $/hr / 9054 tok/s / 3600 * 1M = ~1.69
    },
    "glm-5.2": {
        "cost_per_m_input": 1.50,
        "provider": "self-hosted",
        "label": "GLM-5.2 (self-hosted)",
        "color": "#8B45D6",
        "size": 160,
        # 55 $/hr / ~10000 tok/s / 3600 * 1M = ~1.53
    },
    "qwen3.6-35b": {
        "cost_per_m_input": 0.22,
        "provider": "self-hosted",
        "label": "Qwen 3.6 35B (self-hosted)",
        "color": "#00BCD4",
        "size": 140,
        # 4.50 $/hr / 5700 tok/s / 3600 * 1M = ~0.22
    },
    "mistral-devstral-2-123b": {
        "cost_per_m_input": 0.30,
        "provider": "bedrock",
        "label": "Mistral Devstral 2",
        "color": "#FF9800",
        "size": 100,
    },
    "minimax-m2-5": {
        "cost_per_m_input": 0.25,
        "provider": "bedrock",
        "label": "MiniMax M2.5",
        "color": "#FF5252",
        "size": 100,
    },
    "qwen-qwen3-coder-next": {
        "cost_per_m_input": 0.50,
        "provider": "bedrock",
        "label": "Qwen Coder Next",
        "color": "#4CAF50",
        "size": 110,
    },
}


def load_quality_scores():
    """Load average quality score per model from judge-gpt.json files."""
    scores = {}
    for task_dir in BENCH_DIR.iterdir():
        if not task_dir.is_dir() or task_dir.name in SKIP_DIRS:
            continue
        for model_dir in task_dir.iterdir():
            if not model_dir.is_dir():
                continue
            judge_file = model_dir / "judge-gpt.json"
            if not judge_file.exists():
                continue
            with open(judge_file) as f:
                data = json.load(f)
            model = data.get("model", model_dir.name)
            if model not in scores:
                scores[model] = []
            scores[model].append(data.get("task_score", 0))

    return {m: np.mean(s) for m, s in scores.items()}


def plot_cost_quality(output_dir):
    """Generate cost vs quality scatter plot."""
    quality = load_quality_scores()

    # Combine Kimi K2 variants into one point
    kimi_combined_scores = []
    if "kimi-k2-thinking" in quality:
        kimi_combined_scores.append(quality.pop("kimi-k2-thinking"))
    if "kimi-k2-5" in quality:
        kimi_combined_scores.append(quality.pop("kimi-k2-5"))
    if kimi_combined_scores:
        quality["kimi-k2-combined"] = np.mean(kimi_combined_scores)

    # Add combined Kimi entry to costs
    MODEL_COSTS["kimi-k2-combined"] = {
        "cost_per_m_input": 0.70,
        "provider": "bedrock",
        "label": "Kimi K2/K2.5",
        "color": "#7B7FE8",
        "size": 130,
    }

    fig, ax = plt.subplots(figsize=(12, 8))
    ax.set_facecolor("#F8F8FC")
    fig.patch.set_facecolor("white")

    xs, ys, labels = [], [], []

    for model, avg_score in quality.items():
        if model not in MODEL_COSTS:
            continue
        cost_info = MODEL_COSTS[model]
        x = cost_info["cost_per_m_input"]
        y = avg_score

        marker = "D" if cost_info["provider"] == "self-hosted" else "o"
        ax.scatter(x, y, s=cost_info["size"], c=cost_info["color"],
                   marker=marker, zorder=5, edgecolors="white", linewidths=1.5)

        labels.append((x, y, cost_info["label"], model))

    # Place labels with manual offsets per model to avoid overlaps
    # Offsets: (x_offset, y_offset) from the data point to the label text
    # MiniMax (orange, x=0.25, y=67.9) and Mistral (red, x=0.30, y=67.4) are very close
    # so labels go in opposite directions with no crossing
    label_offsets = {
        "claude-opus-4-8": (0.2, 1.5),
        "kimi-k2-combined": (-0.3, 1.5),
        "kimi-k2-7-code": (0.3, -3.0),
        "glm-5.2": (0.2, 1.5),
        "qwen3.6-35b": (-0.5, -2.5),
        "qwen-qwen3-coder-next": (0.5, 2.0),
        "minimax-m2-5": (-0.25, 1.2),
        "mistral-devstral-2-123b": (-0.25, -1.8),
    }

    for x, y, label, model in labels:
        ox, oy = label_offsets.get(model, (0.2, 1.2))
        # For very close points, place text without arrow to avoid confusion
        if model in ("minimax-m2-5", "mistral-devstral-2-123b"):
            ax.text(x + ox, y + oy, label, fontsize=9, color="#333333",
                    ha="left", va="center")
        else:
            ax.annotate(label, (x, y),
                        xytext=(x + ox, y + oy),
                        fontsize=9, color="#333333",
                        arrowprops=dict(arrowstyle="-", color="#999999", lw=0.5))

        xs.append(x)
        ys.append(y)

    # Draw Pareto frontier (convex hull upper-left boundary)
    points = sorted(zip(xs, ys), key=lambda p: p[0])
    frontier_x, frontier_y = [], []
    max_y = -1
    for px, py in points:
        if py > max_y:
            frontier_x.append(px)
            frontier_y.append(py)
            max_y = py

    if len(frontier_x) > 1:
        # Smooth curve through frontier points
        from scipy.interpolate import make_interp_spline
        try:
            t = np.linspace(0, 1, len(frontier_x))
            t_smooth = np.linspace(0, 1, 100)
            spl_x = make_interp_spline(t, frontier_x, k=min(3, len(frontier_x) - 1))
            spl_y = make_interp_spline(t, frontier_y, k=min(3, len(frontier_y) - 1))
            ax.plot(spl_x(t_smooth), spl_y(t_smooth), "--", color="#E07030",
                    linewidth=2, alpha=0.7, label="Frontier")
        except Exception:
            ax.plot(frontier_x, frontier_y, "--", color="#E07030",
                    linewidth=2, alpha=0.7, label="Frontier")

    # Shaded region below frontier
    ax.fill_between([0] + frontier_x + [max(xs) + 1],
                    [min(ys) - 5] * (len(frontier_x) + 2),
                    [frontier_y[0]] + frontier_y + [frontier_y[-1]],
                    alpha=0.05, color="#E07030")

    # Legend for markers
    ax.scatter([], [], marker="o", c="gray", s=80, label="Bedrock (API)")
    ax.scatter([], [], marker="D", c="gray", s=80, label="Self-hosted (vLLM)")
    ax.plot([], [], "--", color="#E07030", linewidth=2, label="Frontier")
    ax.legend(loc="lower right", fontsize=10, framealpha=0.9)

    ax.set_xlabel("Effective Input Cost ($/1M tokens)", fontsize=11)
    ax.set_ylabel("Avg Quality Score (%)", fontsize=11)
    ax.set_title("Cost vs Quality: Claude Code x Models", fontsize=13, fontweight="bold")
    ax.set_xlim(-0.2, max(xs) + 1.0)
    ax.set_ylim(60, 95)
    ax.grid(True, alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    plt.tight_layout()
    output_path = output_dir / "cost_vs_quality.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate cost vs quality chart.")
    parser.add_argument("--output-dir", default=str(BENCH_DIR / "reports"),
                        help="Output directory (default: .../reports/)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Generating cost vs quality chart...")
    plot_cost_quality(output_dir)
    print("Done.")


if __name__ == "__main__":
    main()
