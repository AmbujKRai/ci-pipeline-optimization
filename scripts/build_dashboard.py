"""Build the static pipeline-performance dashboard published on GitHub Pages.

    python scripts/build_dashboard.py --metrics site/data/metrics.json \
        --docker benchmark/docker-benchmark.json --local docs/results/local_parallelism.json \
        --coverage coverage --out site

Every chart is plain inline SVG with a hover/focus tooltip and a data-table twin, so the page
has no third-party dependencies and works offline.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

C1 = "baseline|cache=off"
C2 = "baseline|cache=on"
C3 = "optimized|cache=off|shards=4"
C4 = "optimized|cache=on|shards=4"
CONFIGS = [
    (C1, "Sequential, no cache (baseline)"),
    (C2, "Sequential + caching"),
    (C3, "Parallel, no cache"),
    (C4, "Parallel + caching (optimized)"),
]
STAGE_NAMES = [
    ("setup", "Python setup"),
    ("static", "Static checks"),
    ("tests", "Tests"),
    ("docker", "Docker build"),
    ("other", "Job overhead"),
]
SCENARIOS = [
    ("cold", "Cold build"),
    ("no_change", "No change"),
    ("code_change", "Code change"),
    ("dependency_change", "Dependency change"),
]

WIDTH = 720


# --- formatting helpers ---------------------------------------------------------------------


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def duration(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}m {rest:02d}s"


def tick_label(seconds: float) -> str:
    """Axis labels: whole minutes read as "2m" rather than "2m 00s"."""
    seconds = round(seconds)
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return duration(seconds)


def tip(*lines: str) -> str:
    return esc(json.dumps(lines))


def nice_step(maximum: float, target: int = 5, durations: bool = False) -> float:
    if maximum <= 0:
        return 1
    if durations:
        for step in (5, 10, 15, 20, 30, 60, 120, 180, 300, 600):
            if maximum / step <= target:
                return step
        return 600
    raw = maximum / target
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 5, 10):
        if raw <= factor * magnitude:
            return factor * magnitude
    return 10 * magnitude


def bar_path(x: float, y: float, width: float, height: float, radius: float = 4) -> str:
    """A bar that is square at the baseline and rounded at its data end."""
    if width <= 0:
        return ""
    r = min(radius, width, height / 2)
    return (
        f"M{x:.1f},{y:.1f} h{width - r:.1f} a{r},{r} 0 0 1 {r},{r} v{height - 2 * r:.1f} "
        f"a{r},{r} 0 0 1 {-r},{r} h{-(width - r):.1f} z"
    )


def empty_state(message: str) -> str:
    return f'<p class="empty">{esc(message)}</p>'


def table(headers: list[str], rows: list[list[str]], numeric_from: int = 1) -> str:
    head = "".join(
        f"<th{' class=num' if i >= numeric_from else ''}>{esc(h)}</th>"
        for i, h in enumerate(headers)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f"<td{' class=num' if i >= numeric_from else ''}>{cell}</td>"
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return (
        "<details><summary>Data table</summary>"
        f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div></details>"
    )


def legend(items: list[tuple[str, str]], shape: str = "rect") -> str:
    keys = "".join(
        f'<span class="key"><span class="swatch {shape}" style="background:var({var})"></span>'
        f"{esc(name)}</span>"
        for name, var in items
    )
    return f'<div class="legend">{keys}</div>'


# --- charts ---------------------------------------------------------------------------------


def hbar_chart(rows: list[tuple[str, float, str]], color: str = "--series-1") -> str:
    """Horizontal bars: rows of (label, seconds, tooltip)."""
    label_w, value_w, row_h, bar_h, top, axis_h = 230, 80, 40, 20, 8, 28
    plot_w = WIDTH - label_w - value_w
    maximum = max(value for _, value, _ in rows)
    step = nice_step(maximum, durations=True)
    scale_max = step * math.ceil(maximum / step)
    height = top + row_h * len(rows) + axis_h
    parts = [f'<svg viewBox="0 0 {WIDTH} {height}" role="img" class="chart">']
    for tick in range(0, int(scale_max) + 1, int(step)):
        x = label_w + plot_w * tick / scale_max
        parts.append(
            f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{height - axis_h}"/>'
        )
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{height - 8}" text-anchor="middle">{tick_label(tick)}</text>'
        )
    for index, (label, value, tooltip) in enumerate(rows):
        y = top + index * row_h + (row_h - bar_h) / 2
        width = plot_w * value / scale_max
        parts.append(
            f'<text class="label" x="{label_w - 12}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="end">{esc(label)}</text>'
        )
        parts.append(
            f'<path class="mark" d="{bar_path(label_w, y, width, bar_h)}" style="fill:var({color})" '
            f'tabindex="0" data-tip="{tooltip}"/>'
        )
        parts.append(
            f'<text class="value" x="{label_w + width + 8:.1f}" y="{y + bar_h / 2 + 4:.1f}">{duration(value)}</text>'
        )
    parts.append(
        f'<line class="axis" x1="{label_w}" x2="{label_w}" y1="{top}" y2="{height - axis_h}"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def stacked_chart(rows: list[tuple[str, list[float]]], series: list[tuple[str, str]]) -> str:
    """Stacked horizontal bars with a 2px surface gap between segments."""
    label_w, value_w, row_h, bar_h, top, axis_h, gap = 230, 80, 40, 20, 8, 28, 2
    plot_w = WIDTH - label_w - value_w
    maximum = max(sum(values) for _, values in rows)
    step = nice_step(maximum, durations=True)
    scale_max = step * math.ceil(maximum / step)
    height = top + row_h * len(rows) + axis_h
    parts = [f'<svg viewBox="0 0 {WIDTH} {height}" role="img" class="chart">']
    for tick in range(0, int(scale_max) + 1, int(step)):
        x = label_w + plot_w * tick / scale_max
        parts.append(
            f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{height - axis_h}"/>'
        )
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{height - 8}" text-anchor="middle">{tick_label(tick)}</text>'
        )
    for index, (label, values) in enumerate(rows):
        y = top + index * row_h + (row_h - bar_h) / 2
        parts.append(
            f'<text class="label" x="{label_w - 12}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="end">{esc(label)}</text>'
        )
        x = label_w
        visible = [i for i, value in enumerate(values) if value > 0]
        for position, i in enumerate(visible):
            width = plot_w * values[i] / scale_max
            last = position == len(visible) - 1
            drawn = width if last else max(width - gap, 0.5)
            name, var = series[i]
            tooltip = tip(f"{duration(values[i])}", f"{name} · {label}")
            if last:
                shape = f'<path class="mark" d="{bar_path(x, y, drawn, bar_h)}"'
            else:
                shape = f'<rect class="mark" x="{x:.1f}" y="{y:.1f}" width="{drawn:.1f}" height="{bar_h}"'
            parts.append(f'{shape} style="fill:var({var})" tabindex="0" data-tip="{tooltip}"/>')
            x += width
        parts.append(
            f'<text class="value" x="{x + 8:.1f}" y="{y + bar_h / 2 + 4:.1f}">{duration(sum(values))}</text>'
        )
    parts.append(
        f'<line class="axis" x1="{label_w}" x2="{label_w}" y1="{top}" y2="{height - axis_h}"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def line_chart(
    points: list[tuple[float, float, str]],
    x_label: str,
    y_format,
    x_ticks: list[float],
    x_format=lambda v: f"{v:g}",
    color: str = "--series-1",
    width: int = 520,
    durations: bool = True,
) -> str:
    """One series over a numeric x axis (points are (x, y, tooltip))."""
    left, right, top, bottom = 64, 24, 16, 48
    height = 260
    plot_w, plot_h = width - left - right, height - top - bottom
    x_min, x_max = min(x_ticks), max(x_ticks)
    y_peak = max(y for _, y, _ in points)
    step = nice_step(y_peak, durations=durations)
    y_max = step * math.ceil(y_peak / step)

    def sx(value: float) -> float:
        return left + plot_w * (value - x_min) / ((x_max - x_min) or 1)

    def sy(value: float) -> float:
        return top + plot_h * (1 - value / y_max)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']
    tick = 0.0
    while tick <= y_max + 1e-9:
        y = sy(tick)
        parts.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{y_format(tick)}</text>'
        )
        tick += step
    for value in x_ticks:
        parts.append(
            f'<text class="tick" x="{sx(value):.1f}" y="{height - bottom + 18}" text-anchor="middle">{esc(x_format(value))}</text>'
        )
    parts.append(
        f'<text class="axis-title" x="{left + plot_w / 2:.1f}" y="{height - 8}" text-anchor="middle">{esc(x_label)}</text>'
    )
    parts.append(
        f'<line class="axis" x1="{left}" x2="{width - right}" y1="{sy(0):.1f}" y2="{sy(0):.1f}"/>'
    )
    path = " ".join(
        f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y, _) in enumerate(points)
    )
    parts.append(f'<path class="line" d="{path}" style="stroke:var({color})"/>')
    for x, y, tooltip in points:
        parts.append(
            f'<circle class="dot" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4" style="fill:var({color})"/>'
        )
        parts.append(
            f'<circle class="hit" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="14" tabindex="0" data-tip="{tooltip}"/>'
        )
    last_x, last_y, _ = points[-1]
    parts.append(
        f'<text class="value" x="{sx(last_x) - 8:.1f}" y="{sy(last_y) - 12:.1f}" text-anchor="end">{y_format(last_y)}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def grouped_chart(rows: list[tuple[str, list[float]]], series: list[tuple[str, str]]) -> str:
    """Horizontal bars grouped per row, one bar per series (2px gap between bars)."""
    label_w, value_w, bar_h, gap, group_gap, top, axis_h = 170, 70, 16, 2, 18, 8, 28
    plot_w = WIDTH - label_w - value_w
    maximum = max(max(values) for _, values in rows)
    step = nice_step(maximum, durations=True)
    scale_max = step * math.ceil(maximum / step)
    group_h = len(series) * bar_h + (len(series) - 1) * gap
    height = top + len(rows) * (group_h + group_gap) + axis_h
    parts = [f'<svg viewBox="0 0 {WIDTH} {height}" role="img" class="chart">']
    plot_bottom = height - axis_h
    for tick in range(0, int(scale_max) + 1, int(step)):
        x = label_w + plot_w * tick / scale_max
        parts.append(
            f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{plot_bottom}"/>'
        )
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{height - 8}" text-anchor="middle">{tick_label(tick)}</text>'
        )
    for index, (label, values) in enumerate(rows):
        group_top = top + index * (group_h + group_gap) + group_gap / 2
        parts.append(
            f'<text class="label" x="{label_w - 12}" y="{group_top + group_h / 2 + 4:.1f}" text-anchor="end">{esc(label)}</text>'
        )
        for s, (name, var) in enumerate(series):
            y = group_top + s * (bar_h + gap)
            width = max(plot_w * values[s] / scale_max, 1)
            tooltip = tip(f"{values[s]:.1f} s", f"{name} · {label}")
            parts.append(
                f'<path class="mark" d="{bar_path(label_w, y, width, bar_h)}" style="fill:var({var})" tabindex="0" data-tip="{tooltip}"/>'
            )
            parts.append(
                f'<text class="value small" x="{label_w + width + 6:.1f}" y="{y + bar_h - 4:.1f}">{values[s]:.1f}s</text>'
            )
    parts.append(
        f'<line class="axis" x1="{label_w}" x2="{label_w}" y1="{top}" y2="{plot_bottom}"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def timeline_chart(series: list[tuple[str, str, list[tuple[datetime, float, str]]]]) -> str:
    """Dots over time, one colour per pipeline (series are (name, colour var, points))."""
    width, height, left, right, top, bottom = WIDTH, 280, 64, 24, 16, 40
    plot_w, plot_h = width - left - right, height - top - bottom
    all_points = [point for _, _, points in series for point in points]
    t_min = min(p[0] for p in all_points).timestamp()
    t_max = max(p[0] for p in all_points).timestamp()
    span = (t_max - t_min) or 3600
    peak = max(p[1] for p in all_points)
    step = nice_step(peak, durations=True)
    y_max = step * math.ceil(peak / step)

    def sx(moment: datetime) -> float:
        return left + plot_w * (moment.timestamp() - t_min) / span

    def sy(value: float) -> float:
        return top + plot_h * (1 - value / y_max)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']
    tick = 0
    while tick <= y_max:
        y = sy(tick)
        parts.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{tick_label(tick)}</text>'
        )
        tick += step
    first = min(p[0] for p in all_points)
    last = max(p[0] for p in all_points)
    parts.append(f'<text class="tick" x="{left}" y="{height - 12}">{first:%d %b %H:%M}</text>')
    parts.append(
        f'<text class="tick" x="{width - right}" y="{height - 12}" text-anchor="end">{last:%d %b %H:%M} UTC</text>'
    )
    parts.append(
        f'<line class="axis" x1="{left}" x2="{width - right}" y1="{sy(0):.1f}" y2="{sy(0):.1f}"/>'
    )
    for _, var, points in series:
        for moment, value, tooltip in points:
            parts.append(
                f'<circle class="dot" cx="{sx(moment):.1f}" cy="{sy(value):.1f}" r="4" style="fill:var({var})"/>'
            )
            parts.append(
                f'<circle class="hit" cx="{sx(moment):.1f}" cy="{sy(value):.1f}" r="12" tabindex="0" data-tip="{tooltip}"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


# --- page sections --------------------------------------------------------------------------


def coverage_percent(coverage_dir: Path | None) -> float | None:
    if coverage_dir is None:
        return None
    report = coverage_dir / "coverage.xml"
    if not report.exists():
        return None
    # coverage.xml is produced by our own CI job.
    root = ET.parse(report).getroot()  # nosec B314
    return round(float(root.get("line-rate", 0)) * 100, 1)


def kpis(summary: dict[str, Any], coverage: float | None, test_count: int | None) -> str:
    base, best = summary.get(C1), summary.get(C4)
    if not base or not best:
        return empty_state(
            "Speed-up figures appear once both the baseline and the optimized pipeline have run."
        )
    speedup = base["median_wall_seconds"] / best["median_wall_seconds"]
    saved = base["median_wall_seconds"] - best["median_wall_seconds"]
    runner_change = best["median_runner_seconds"] / base["median_runner_seconds"]
    tiles = [
        (
            "Baseline pipeline",
            duration(base["median_wall_seconds"]),
            f"median of {base['runs']} runs",
        ),
        (
            "Optimized pipeline",
            duration(best["median_wall_seconds"]),
            f"median of {best['runs']} runs",
        ),
        ("Time saved per run", duration(saved), "wall-clock, per push"),
        (
            "Runner time (optimized)",
            duration(best["median_runner_seconds"]),
            f"{runner_change:.1f}× the baseline's {duration(base['median_runner_seconds'])} of compute",
        ),
    ]
    if coverage is not None:
        tiles.append(("Test coverage", f"{coverage:.0f}%", "merged across all test shards"))
    tile_html = "".join(
        f'<article class="tile"><h3>{esc(label)}</h3><p class="tile-value">{esc(value)}</p>'
        f'<p class="muted">{esc(note)}</p></article>'
        for label, value, note in tiles
    )
    return (
        '<section class="hero">'
        f'<div class="hero-figure"><p class="hero-label">CI speed-up</p>'
        f'<p class="hero-value">{speedup:.1f}×</p>'
        f'<p class="muted">faster than the baseline (median wall-clock time)</p></div>'
        f'<div class="tiles">{tile_html}</div></section>'
    )


def config_section(summary: dict[str, Any]) -> str:
    present = [(key, label) for key, label in CONFIGS if key in summary]
    if not present:
        return empty_state("No completed pipeline runs yet.")
    rows = []
    for key, label in present:
        item = summary[key]
        rows.append(
            (
                label,
                item["median_wall_seconds"],
                tip(
                    duration(item["median_wall_seconds"]),
                    label,
                    f"middle 50%: {duration(item['p25_wall_seconds'])}–{duration(item['p75_wall_seconds'])}",
                    f"{item['runs']} runs",
                ),
            )
        )
    data = [
        [
            esc(label),
            duration(summary[key]["median_wall_seconds"]),
            f"{duration(summary[key]['p25_wall_seconds'])}–{duration(summary[key]['p75_wall_seconds'])}",
            duration(summary[key]["median_runner_seconds"]),
            str(summary[key]["runs"]),
        ]
        for key, label in present
    ]
    return hbar_chart(rows) + table(
        ["Configuration", "Median wall time", "Middle 50%", "Median runner time", "Runs"], data
    )


def stage_section(summary: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    present = [(key, label) for key, label in CONFIGS if key in summary]
    if not present:
        return empty_state("No completed pipeline runs yet.")
    series = [(name, f"--series-{i + 1}") for i, (_, name) in enumerate(STAGE_NAMES)]
    rows, data = [], []
    for key, label in present:
        matching = [run for run in runs if run["config"] == key]
        medians = []
        for stage, _ in STAGE_NAMES[:-1]:
            values = sorted(run["stages_summed"][stage] for run in matching)
            medians.append(values[len(values) // 2])
        runner = summary[key]["median_runner_seconds"]
        medians.append(max(runner - sum(medians), 0))
        rows.append((label, medians))
        data.append([esc(label), *[f"{value:.0f}s" for value in medians], duration(runner)])
    return (
        legend(series)
        + stacked_chart(rows, series)
        + table(["Configuration", *[name for _, name in STAGE_NAMES], "Total"], data)
    )


def shard_section(summary: dict[str, Any]) -> str:
    points = sorted(
        (item["shards"], item)
        for key, item in summary.items()
        if key.startswith("optimized|cache=on|shards=")
    )
    if len(points) < 2:
        return empty_state("Run the shard-scaling experiment (E2) to fill this chart.")
    shards = [float(s) for s, _ in points]
    wall = [
        (
            float(s),
            item["median_wall_seconds"],
            tip(duration(item["median_wall_seconds"]), f"{s} shards · wall time"),
        )
        for s, item in points
    ]
    runner = [
        (
            float(s),
            item["median_runner_seconds"] / 60,
            tip(f"{item['median_runner_seconds'] / 60:.1f} runner-min", f"{s} shards · compute"),
        )
        for s, item in points
    ]
    charts = (
        '<div class="pair">'
        f"<figure><figcaption>Wall-clock time (how long you wait)</figcaption>{line_chart(wall, 'Test shards', duration, shards)}</figure>"
        f"<figure><figcaption>Runner minutes (what you pay for)</figcaption>"
        f"{line_chart(runner, 'Test shards', lambda v: f'{v:.0f} min' if v >= 1 else f'{v * 60:.0f}s', shards, color='--series-2', durations=False)}</figure>"
        "</div>"
    )
    data = [
        [
            str(s),
            duration(item["median_wall_seconds"]),
            f"{item['median_runner_seconds'] / 60:.1f}",
            str(item["runs"]),
        ]
        for s, item in points
    ]
    return charts + table(["Shards", "Median wall time", "Runner minutes", "Runs"], data)


def local_section(local: dict[str, Any] | None) -> str:
    if not local:
        return empty_state("Run scripts/benchmark_local.py to record this experiment (E3).")
    results = local["results"]
    points = [
        (
            float(max(row["workers"], 1)),
            row["median"],
            tip(f"{row['median']:.1f}s", f"{row['label']} · {row['speedup']}× speed-up"),
        )
        for row in results
    ]
    ticks = [float(max(row["workers"], 1)) for row in results]
    chart = line_chart(
        points,
        "pytest-xdist workers (1 = serial)",
        duration,
        ticks,
        x_format=lambda v: f"{v:g}",
        width=WIDTH,
    )
    data = [
        [
            esc(row["label"]),
            f"{row['median']:.1f}s",
            f"{row['speedup']}×",
            f"{row['efficiency']:.2f}",
        ]
        for row in results
    ]
    machine = local.get("machine", {})
    note = (
        f'<p class="muted">Measured on {esc(machine.get("logical_cpus", "?"))} logical CPUs '
        f"({esc(machine.get('os', 'unknown OS'))}), median of 3 runs each.</p>"
    )
    return chart + note + table(["Workers", "Median wall time", "Speed-up", "Efficiency"], data)


def docker_section(docker: dict[str, Any] | None) -> str:
    if not docker:
        return empty_state("Run the “Docker Cache Benchmark” workflow (E4) to fill this chart.")
    median = docker["median"]
    series = [("Optimized Dockerfile", "--series-1"), ("Naive Dockerfile", "--series-2")]
    rows = [(label, [median["optimized"][key], median["naive"][key]]) for key, label in SCENARIOS]
    data = [
        [esc(label), f"{median['naive'][key]:.1f}s", f"{median['optimized'][key]:.1f}s"]
        for key, label in SCENARIOS
    ]
    naive_mb = median["naive"]["size_bytes"] / 1e6
    optimized_mb = median["optimized"]["size_bytes"] / 1e6
    data.append(["Image size", f"{naive_mb:.0f} MB", f"{optimized_mb:.0f} MB"])
    return (
        legend(series)
        + grouped_chart(rows, series)
        + f'<p class="muted">Image size: {optimized_mb:.0f} MB optimized vs {naive_mb:.0f} MB naive '
        f"(median of {docker['repeats']} repeats on a GitHub runner).</p>"
        + table(["Scenario", "Naive", "Optimized"], data)
    )


def history_section(runs: list[dict[str, Any]]) -> str:
    def points(config: str) -> list[tuple[datetime, float, str]]:
        return [
            (
                datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")),
                run["wall_seconds"],
                tip(
                    duration(run["wall_seconds"]),
                    f"{run['pipeline']} · {run['sha']} · {run['event']}",
                ),
            )
            for run in runs
            if run["config"] == config
        ]

    baseline, optimized = points(C1), points(C4)
    if not baseline and not optimized:
        return empty_state("No runs yet.")
    series = [("Optimized", "--series-1", optimized), ("Baseline", "--series-2", baseline)]
    series = [entry for entry in series if entry[2]]
    return legend([(name, var) for name, var, _ in series], shape="dot") + timeline_chart(series)


def runs_table(runs: list[dict[str, Any]]) -> str:
    recent = sorted(runs, key=lambda run: run["created_at"], reverse=True)[:20]
    rows = [
        [
            esc(run["created_at"][:16].replace("T", " ")),
            esc(run["pipeline"]),
            esc(run["config"].split("|", 1)[1].replace("|", " · ")),
            esc(run["event"]),
            duration(run["wall_seconds"]),
            duration(run["runner_seconds"]),
            f'<a href="{esc(run["url"])}">{esc(run["sha"])}</a>',
        ]
        for run in recent
    ]
    head = "".join(
        f"<th{' class=num' if i in (4, 5) else ''}>{h}</th>"
        for i, h in enumerate(
            ["Started (UTC)", "Pipeline", "Settings", "Trigger", "Wall time", "Runner time", "Run"]
        )
    )
    body = "".join(
        "<tr>"
        + "".join(f"<td{' class=num' if i in (4, 5) else ''}>{c}</td>" for i, c in enumerate(row))
        + "</tr>"
        for row in rows
    )
    return f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


# --- page -----------------------------------------------------------------------------------

STYLE = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --text-1: #0b0b0b; --text-2: #52514e; --muted: #6f6d68;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10); --link: #1c5cab;
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #eda100; --series-5: #e87ba4;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --text-1: #ffffff; --text-2: #c3c2b7; --muted: #a3a199;
    --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10); --link: #86b6ef;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500; --series-5: #d55181;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --text-1: #ffffff; --text-2: #c3c2b7; --muted: #a3a199;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10); --link: #86b6ef;
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500; --series-5: #d55181;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--text-1);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
a { color: var(--link); }
main { width: min(1120px, 100% - 32px); margin: 0 auto; padding: 32px 0 56px; }
header.page { margin-bottom: 24px; }
header.page h1 { font-size: 1.75rem; margin: 0 0 6px; }
header.page p { margin: 0; color: var(--text-2); max-width: 70ch; }
.links { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 12px; }
.muted { color: var(--muted); font-size: 0.9rem; }
.card { background: var(--surface); border: 1px solid var(--ring); border-radius: 12px;
  padding: 20px 22px; margin-bottom: 20px; }
.card h2 { font-size: 1.1rem; margin: 0 0 4px; }
.card > p.lede { margin: 0 0 14px; color: var(--text-2); max-width: 80ch; }
.hero { display: grid; grid-template-columns: minmax(220px, 300px) 1fr; gap: 20px; margin-bottom: 20px; }
.hero-figure, .tile { background: var(--surface); border: 1px solid var(--ring); border-radius: 12px; padding: 18px 20px; }
.hero-label { margin: 0; color: var(--text-2); font-weight: 600; }
.hero-value { font-size: 4rem; font-weight: 700; line-height: 1.05; margin: 6px 0; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.tile h3 { margin: 0; font-size: 0.85rem; color: var(--text-2); font-weight: 600; }
.tile-value { font-size: 1.6rem; font-weight: 700; margin: 4px 0 2px; }
.tile p { margin: 0; }
svg.chart { width: 100%; height: auto; display: block; overflow: visible; }
svg .grid { stroke: var(--grid); stroke-width: 1; }
svg .axis { stroke: var(--axis); stroke-width: 1; }
svg .tick, svg .axis-title { fill: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
svg .label { fill: var(--text-2); font-size: 13px; }
svg .value { fill: var(--text-1); font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
svg .value.small { font-size: 11.5px; font-weight: 500; fill: var(--text-2); }
svg .line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
svg .dot { stroke: var(--surface); stroke-width: 2; }
svg .hit { fill: transparent; cursor: pointer; }
svg .mark { cursor: pointer; transition: opacity 120ms; }
svg .mark:hover, svg .mark:focus { opacity: 0.8; outline: none; }
svg .hit:focus { outline: none; stroke: var(--text-2); stroke-width: 1; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 0 0 10px; font-size: 0.88rem; color: var(--text-2); }
.key { display: inline-flex; align-items: center; gap: 6px; }
.swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px; }
.swatch.dot { border-radius: 50%; width: 10px; height: 10px; }
.pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
figure { margin: 0; }
figcaption { font-size: 0.88rem; color: var(--text-2); margin-bottom: 6px; }
details { margin-top: 10px; }
summary { cursor: pointer; color: var(--text-2); font-size: 0.88rem; }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.88rem; margin-top: 8px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th { color: var(--text-2); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.empty { color: var(--muted); font-style: italic; margin: 8px 0; }
.method li { margin-bottom: 6px; }
#tooltip { position: fixed; pointer-events: none; z-index: 10; background: var(--surface); color: var(--text-1);
  border: 1px solid var(--ring); border-radius: 8px; padding: 8px 10px; font-size: 0.85rem;
  box-shadow: 0 4px 16px rgba(0,0,0,0.12); max-width: 280px; display: none; }
#tooltip strong { display: block; font-size: 1rem; }
#tooltip span { color: var(--text-2); display: block; }
@media (max-width: 720px) { .hero { grid-template-columns: 1fr; } .hero-value { font-size: 3rem; } }
"""

SCRIPT = """
(() => {
  const box = document.getElementById('tooltip');
  const show = (el, x, y) => {
    const lines = JSON.parse(el.dataset.tip);
    box.replaceChildren();
    lines.forEach((line, i) => {
      const node = document.createElement(i === 0 ? 'strong' : 'span');
      node.textContent = line;
      box.appendChild(node);
    });
    box.style.display = 'block';
    const pad = 14, w = box.offsetWidth, h = box.offsetHeight;
    box.style.left = Math.min(x + pad, window.innerWidth - w - 8) + 'px';
    box.style.top = Math.max(y - h - pad, 8) + 'px';
  };
  const hide = () => { box.style.display = 'none'; };
  document.querySelectorAll('[data-tip]').forEach((el) => {
    el.addEventListener('pointermove', (e) => show(el, e.clientX, e.clientY));
    el.addEventListener('pointerleave', hide);
    el.addEventListener('focus', () => { const r = el.getBoundingClientRect(); show(el, r.right, r.top); });
    el.addEventListener('blur', hide);
  });
})();
"""


def page(
    metrics: dict[str, Any],
    local: dict[str, Any] | None,
    docker: dict[str, Any] | None,
    coverage: float | None,
    repo_url: str,
    app_url: str | None,
) -> str:
    summary, runs = metrics["summary"], metrics["runs"]
    generated = metrics["generated_at"].replace("T", " ").replace("+00:00", " UTC")
    test_count = None
    links = [
        f'<a href="{esc(repo_url)}">Source code</a>',
        f'<a href="{esc(repo_url)}/actions">GitHub Actions runs</a>',
        '<a href="coverage/index.html">Coverage report</a>',
        '<a href="data/metrics.json">Raw data (JSON)</a>',
    ]
    if app_url:
        links.insert(0, f'<a href="{esc(app_url)}">Live application</a>')
    sections = [
        (
            "Pipeline time by configuration",
            "Experiment E1: the same work (lint, security scan, 581 tests with coverage, Docker build"
            " and container smoke test) under four set-ups. Bars show the median wall-clock time.",
            config_section(summary),
        ),
        (
            "Where the compute goes",
            "Runner time summed over every job (what a paid CI plan bills). Caching removes most of the"
            " setup and Docker time; parallel jobs add some per-job overhead in exchange for speed.",
            stage_section(summary, runs),
        ),
        (
            "How many shards?",
            "Experiment E2: the optimized pipeline with 1 to 8 test shards. Waiting time stops"
            " improving once fixed per-job costs dominate (Amdahl's law), while compute keeps growing.",
            shard_section(summary),
        ),
        (
            "Parallel workers on one machine",
            "Experiment E3: the full test suite with pytest-xdist on a laptop, serial versus 2, 4"
            " and 8 worker processes.",
            local_section(local),
        ),
        (
            "Docker layer caching",
            "Experiment E4: build time of a naive Dockerfile versus the cache-friendly multi-stage"
            " Dockerfile, starting from an empty cache.",
            docker_section(docker),
        ),
        (
            "Run history",
            "Every successful run of the baseline and the optimized pipeline (default settings).",
            history_section(runs),
        ),
        ("Recent runs", "", runs_table(runs)),
    ]
    cards = "".join(
        f'<section class="card"><h2>{esc(title)}</h2>'
        + (f'<p class="lede">{esc(lede)}</p>' if lede else "")
        + body
        + "</section>"
        for title, lede, body in sections
    )
    method = """
<section class="card method"><h2>How the numbers are measured</h2><ul>
<li><strong>Wall-clock time</strong> runs from the first job starting to the last job finishing,
so runner queueing before the pipeline starts is excluded.</li>
<li><strong>Runner time</strong> is the sum of all job durations.</li>
<li>Only successful first attempts count; re-runs and failed runs are excluded.</li>
<li>Medians are used throughout, because shared CI runners are noisy.</li>
<li>Warm-up runs that only fill the caches are labelled and left out.</li>
<li>External-service tests use stubs with simulated network latency; all other test time is
real computation.</li>
</ul></section>"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CI Pipeline Metrics</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<header class="page">
<h1>CI pipeline optimization: results</h1>
<p>Parallel test execution and build caching on the ShopLite pipeline, measured on GitHub-hosted
runners. Updated automatically after every pipeline run. Last update: {esc(generated)}.</p>
<div class="links">{"".join(links)}</div>
</header>
{kpis(summary, coverage, test_count)}
{cards}
{method}
</main>
<div id="tooltip" role="status"></div>
<script>{SCRIPT}</script>
</body>
</html>
"""


def badge(path: Path, label: str, message: str, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schemaVersion": 1, "label": label, "message": message, "color": color}
    path.write_text(json.dumps(payload), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the pipeline metrics dashboard")
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--local", type=Path)
    parser.add_argument("--docker", type=Path)
    parser.add_argument("--coverage", type=Path, help="folder with htmlcov/ and coverage.xml")
    parser.add_argument(
        "--repo-url", default="https://github.com/AmbujKRai/ci-pipeline-optimization"
    )
    parser.add_argument("--app-url", default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    def load(path: Path | None) -> dict[str, Any] | None:
        if path and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    metrics = load(args.metrics) or {"summary": {}, "runs": [], "generated_at": "never"}
    coverage = coverage_percent(args.coverage)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(
        page(metrics, load(args.local), load(args.docker), coverage, args.repo_url, args.app_url),
        encoding="utf-8",
    )
    (args.out / ".nojekyll").write_text("", encoding="utf-8")

    summary = metrics["summary"]
    if C1 in summary and C4 in summary:
        speedup = summary[C1]["median_wall_seconds"] / summary[C4]["median_wall_seconds"]
        badge(args.out / "badges" / "speedup.json", "CI speed-up", f"{speedup:.1f}x faster", "blue")
        optimized = duration(summary[C4]["median_wall_seconds"])
        badge(args.out / "badges" / "pipeline.json", "optimized pipeline", optimized, "blue")
    if coverage is not None:
        badge(args.out / "badges" / "coverage.json", "coverage", f"{coverage:.0f}%", "brightgreen")
    if args.coverage and (args.coverage / "htmlcov").exists():
        shutil.copytree(args.coverage / "htmlcov", args.out / "coverage", dirs_exist_ok=True)
    print(f"dashboard written to {args.out / 'index.html'}")


if __name__ == "__main__":
    main()
