#!/usr/bin/env python3
"""Generate benchmark report charts from judge.json files.

Reads all judge results, produces:
1. Score matrix heatmap (tasks × models)
2. Per-model bar chart (average scores)
3. Per-task bar chart (difficulty ranking)
4. Radar chart (per-criteria breakdown for top models)
5. CSV export of all scores

No LLM is used — this is purely data visualization from the JSON files.

Usage:
    python3 generate-report.py [--output-dir ./reports]

Requires: matplotlib, pandas (install with: pip install matplotlib pandas)
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    import numpy as np
except ImportError:
    print("Error: matplotlib and numpy required. Install with: pip install matplotlib numpy", file=sys.stderr)
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("Error: pandas required. Install with: pip install pandas", file=sys.stderr)
    sys.exit(1)


BENCH_DIR = Path(__file__).parent.parent / "swe-benchmark-data" / "mcp-gateway-registry"

TASK_ORDER = [
    "remove-faiss",
    "remove-efs-from-terraform-aws-ecs",
    "ssrf-hardening-outbound-url-validation",
    "migrate-ecs-env-vars-to-secrets-manager",
    "replace-keycloak-db-password-with-rds-iam",
]

TASK_SHORT = {
    "remove-faiss": "Remove FAISS",
    "remove-efs-from-terraform-aws-ecs": "Remove EFS",
    "ssrf-hardening-outbound-url-validation": "SSRF Hardening",
    "migrate-ecs-env-vars-to-secrets-manager": "Migrate Secrets",
    "replace-keycloak-db-password-with-rds-iam": "Keycloak IAM",
}

SKIP_DIRS = {"repo", "implementations"}


def load_all_scores():
    """Load all judge.json files into a DataFrame."""
    rows = []
    for task_dir in BENCH_DIR.iterdir():
        if not task_dir.is_dir() or task_dir.name in SKIP_DIRS:
            continue
        task = task_dir.name
        for model_dir in task_dir.iterdir():
            if not model_dir.is_dir():
                continue
            judge_file = model_dir / "judge.json"
            if not judge_file.exists():
                continue
            with open(judge_file) as f:
                data = json.load(f)
            row = {
                "task": task,
                "model": data.get("model", model_dir.name),
                "task_score": data.get("task_score", 0),
            }
            scores = data.get("scores", {})
            for artifact in ["github_issue", "lld", "review", "testing"]:
                artifact_scores = scores.get(artifact, {})
                row[f"{artifact}_total"] = artifact_scores.get("total", 0)
                for criterion in ["completeness", "correctness", "specificity", "risk_awareness"]:
                    row[f"{artifact}_{criterion}"] = artifact_scores.get(criterion, 0)
            rows.append(row)

    df = pd.DataFrame(rows)
    return df


def build_matrix(df):
    """Build task × model score matrix."""
    pivot = df.pivot_table(index="task", columns="model", values="task_score", aggfunc="first")
    pivot = pivot.reindex(index=[t for t in TASK_ORDER if t in pivot.index])
    pivot = pivot.reindex(columns=sorted(pivot.columns, key=lambda m: -pivot[m].mean()))
    return pivot


def plot_heatmap(matrix, output_dir):
    """Generate score matrix heatmap."""
    fig, ax = plt.subplots(figsize=(14, 6))

    short_tasks = [TASK_SHORT.get(t, t) for t in matrix.index]
    data = matrix.values

    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=50, vmax=95)

    ax.set_xticks(range(len(matrix.columns)))
    ax.set_xticklabels(matrix.columns, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(short_tasks)))
    ax.set_yticklabels(short_tasks, fontsize=10)

    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            val = data[i, j]
            if not np.isnan(val):
                color = "white" if val < 65 else "black"
                ax.text(j, i, f"{val:.1f}", ha="center", va="center", fontsize=8, color=color)

    plt.colorbar(im, ax=ax, label="Task Score (0-100)")
    ax.set_title("SWE Benchmark: Task Score Matrix (Tasks x Models)", fontsize=12, pad=15)
    plt.tight_layout()
    plt.savefig(output_dir / "heatmap.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {output_dir / 'heatmap.png'}")


def plot_leaderboard(df, output_dir):
    """Generate per-model average score bar chart."""
    avg_scores = df.groupby("model")["task_score"].mean().sort_values(ascending=True)

    fig, ax = plt.subplots(figsize=(10, 6))
    colors = plt.cm.RdYlGn(np.linspace(0.3, 0.9, len(avg_scores)))
    bars = ax.barh(range(len(avg_scores)), avg_scores.values, color=colors)

    ax.set_yticks(range(len(avg_scores)))
    ax.set_yticklabels(avg_scores.index, fontsize=9)
    ax.set_xlabel("Average Score (0-100)")
    ax.set_title("SWE Benchmark: Model Leaderboard", fontsize=12)
    ax.set_xlim(50, 95)

    for i, (val, bar) in enumerate(zip(avg_scores.values, bars)):
        ax.text(val + 0.5, i, f"{val:.1f}", va="center", fontsize=9)

    plt.tight_layout()
    plt.savefig(output_dir / "leaderboard.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {output_dir / 'leaderboard.png'}")


def plot_task_difficulty(df, output_dir):
    """Generate per-task average score (difficulty indicator)."""
    task_avg = df.groupby("task")["task_score"].mean()
    task_avg = task_avg.reindex([t for t in TASK_ORDER if t in task_avg.index])
    short_names = [TASK_SHORT.get(t, t) for t in task_avg.index]

    fig, ax = plt.subplots(figsize=(8, 5))
    colors = plt.cm.RdYlGn(np.linspace(0.3, 0.9, len(task_avg)))
    bars = ax.bar(range(len(task_avg)), task_avg.values, color=colors)

    ax.set_xticks(range(len(task_avg)))
    ax.set_xticklabels(short_names, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Average Score Across All Models")
    ax.set_title("SWE Benchmark: Task Difficulty (lower = harder)", fontsize=12)
    ax.set_ylim(50, 90)

    for i, val in enumerate(task_avg.values):
        ax.text(i, val + 0.5, f"{val:.1f}", ha="center", fontsize=9)

    plt.tight_layout()
    plt.savefig(output_dir / "task_difficulty.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {output_dir / 'task_difficulty.png'}")


def plot_criteria_radar(df, output_dir, top_n=4):
    """Radar chart showing per-criteria strengths for top models."""
    criteria = ["completeness", "correctness", "specificity", "risk_awareness"]
    artifacts = ["github_issue", "lld", "review", "testing"]

    avg_scores = df.groupby("model")["task_score"].mean().sort_values(ascending=False)
    top_models = avg_scores.head(top_n).index.tolist()

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))

    angles = np.linspace(0, 2 * np.pi, len(criteria), endpoint=False).tolist()
    angles += angles[:1]

    for model in top_models:
        model_df = df[df["model"] == model]
        values = []
        for criterion in criteria:
            cols = [f"{a}_{criterion}" for a in artifacts]
            avg = model_df[cols].mean(axis=1).mean()
            values.append(avg)
        values += values[:1]
        ax.plot(angles, values, "o-", linewidth=2, label=model)
        ax.fill(angles, values, alpha=0.1)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels([c.replace("_", " ").title() for c in criteria], fontsize=10)
    ax.set_ylim(10, 25)
    ax.set_title("Per-Criteria Strengths (Top Models)", fontsize=12, pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.0), fontsize=9)

    plt.tight_layout()
    plt.savefig(output_dir / "criteria_radar.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {output_dir / 'criteria_radar.png'}")


def plot_artifact_breakdown(df, output_dir):
    """Grouped bar chart: per-artifact scores for each model."""
    artifacts = ["github_issue", "lld", "review", "testing"]
    artifact_labels = ["GitHub Issue", "LLD", "Review", "Testing"]

    avg_scores = df.groupby("model")["task_score"].mean().sort_values(ascending=False)
    models = avg_scores.index.tolist()

    fig, ax = plt.subplots(figsize=(14, 6))
    x = np.arange(len(models))
    width = 0.2

    for i, (artifact, label) in enumerate(zip(artifacts, artifact_labels)):
        col = f"{artifact}_total"
        means = [df[df["model"] == m][col].mean() for m in models]
        ax.bar(x + i * width, means, width, label=label)

    ax.set_xticks(x + width * 1.5)
    ax.set_xticklabels(models, rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Average Artifact Score (0-100)")
    ax.set_title("SWE Benchmark: Per-Artifact Scores by Model", fontsize=12)
    ax.set_ylim(50, 95)
    ax.legend()

    plt.tight_layout()
    plt.savefig(output_dir / "artifact_breakdown.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {output_dir / 'artifact_breakdown.png'}")


def export_csv(df, matrix, output_dir):
    """Export raw data as CSV."""
    df.to_csv(output_dir / "all_scores.csv", index=False)
    matrix.to_csv(output_dir / "score_matrix.csv")
    print(f"  Saved: {output_dir / 'all_scores.csv'}")
    print(f"  Saved: {output_dir / 'score_matrix.csv'}")


def main():
    parser = argparse.ArgumentParser(description="Generate benchmark report from judge results.")
    parser.add_argument("--output-dir", default=str(BENCH_DIR / "reports"),
                        help="Output directory for charts and CSV (default: .../reports/)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading judge scores...")
    df = load_all_scores()
    print(f"  Found {len(df)} scored (task × model) cells")
    print(f"  Models: {sorted(df['model'].unique())}")
    print(f"  Tasks: {sorted(df['task'].unique())}")

    matrix = build_matrix(df)

    print("\nGenerating charts...")
    plot_heatmap(matrix, output_dir)
    plot_leaderboard(df, output_dir)
    plot_task_difficulty(df, output_dir)
    plot_criteria_radar(df, output_dir)
    plot_artifact_breakdown(df, output_dir)

    print("\nExporting data...")
    export_csv(df, matrix, output_dir)

    print(f"\nDone. All outputs in: {output_dir}")


if __name__ == "__main__":
    main()
