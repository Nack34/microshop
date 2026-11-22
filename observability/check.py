"""Static checks for the observability config, runnable without Prometheus or Grafana.

1. Every PromQL expression (alert rules + dashboard panels) parses.
2. Every metric it references is actually exported by the services. Pass the metric names
   seen on the live /metrics endpoints with --metrics (the e2e test does this), so a renamed
   metric breaks the build instead of silently leaving an empty panel.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import promql_parser
import yaml

ROOT = pathlib.Path(__file__).parent
# Series Prometheus creates itself, plus histogram/counter suffixes.
BUILTIN = {"up"}
SUFFIXES = ("_bucket", "_count", "_sum", "_created")


def expressions() -> list[tuple[str, str]]:
    found = []
    rules = yaml.safe_load((ROOT / "prometheus/alerts.yml").read_text())
    for group in rules["groups"]:
        for rule in group["rules"]:
            found.append((f"alert {rule['alert']}", rule["expr"]))
    dashboard = json.loads((ROOT / "grafana/dashboards/microshop.json").read_text())
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            found.append((f"panel '{panel['title']}'", target["expr"]))
    return found


def metric_names(node) -> set[str]:
    """Walks the parsed expression and returns the metric names of every selector in it."""
    names: set[str] = set()
    kind = type(node).__name__
    if kind == "VectorSelector":
        names.add(node.name)
    for attr in ("lhs", "rhs", "expr", "vector_selector", "param"):
        child = getattr(node, attr, None)
        if child is not None and not isinstance(child, (str, int, float)):
            names |= metric_names(child)
    for arg in getattr(node, "args", None) or []:
        names |= metric_names(arg)
    return names


def base_name(name: str) -> str:
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    return name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", type=pathlib.Path, help="file with one exported metric name per line")
    args = parser.parse_args()
    exported = set(args.metrics.read_text().split()) if args.metrics else None

    errors = []
    referenced = 0
    for where, expr in expressions():
        try:
            names = metric_names(promql_parser.parse(expr))
        except Exception as exc:  # noqa: BLE001 - report every bad expression, not just the first
            errors.append(f"{where}: does not parse: {exc}")
            continue
        referenced += len(names)
        if exported is not None:
            for name in names - BUILTIN:
                if name not in exported and base_name(name) not in exported:
                    errors.append(f"{where}: metric '{name}' is not exported by any service")
    for error in errors:
        print("✗", error)
    if not errors:
        print(f"✓ {len(expressions())} PromQL expressions parse; {referenced} metric references checked")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
