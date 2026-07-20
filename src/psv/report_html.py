"""HTML divergence report export (T-14)."""
from __future__ import annotations

import html
from typing import Any


def render_divergence_html(divergences: list[dict[str, Any]]) -> str:
    """Render a self-contained HTML divergence report."""
    rows = ""
    for d in divergences:
        pid = html.escape(str(d.get("id", "?")))
        chain = html.escape(str(d.get("chain", "?")))
        sut = html.escape(str(d.get("sut", "?")))
        dtype = html.escape(str(d.get("type", "?")))
        rows += f"<tr><td>{pid}</td><td>{chain}</td><td>{sut}</td><td>{dtype}</td></tr>"

    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>PSV Divergence Report</title>"
        "<style>body{{font-family:system-ui,sans-serif;max-width:960px;margin:2em auto}}"
        "table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border:1px solid #ccc}}"
        "</style></head><body><h1>PSV Divergence Report</h1>"
        "<table><thead><tr><th>ID</th><th>Chain</th><th>SUT</th><th>Type</th></tr></thead><tbody>"
        f"{rows}</tbody></table></body></html>"
    )
