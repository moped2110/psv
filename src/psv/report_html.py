# src/psv/report_html.py
def generate_html(divergences):
    rows = ""
    for d in divergences:
        rows += f"<tr><td>{d['id']}</td><td>{d['chain']}</td><td>{d['sut']}</td><td>{d['type']}</td></tr>"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>PSV Report</title><style>table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:8px}}</style></head><body><h1>Reconciliation Report</h1><table><tr><th>Payment-ID</th><th>Chain-Status</th><th>SUT-Status</th><th>Divergenz-Typ</th></tr>{rows}</table></body></html>"""

