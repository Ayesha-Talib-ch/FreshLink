"""CSV and PDF export of the admin reports (spec 14.3 "exported as CSV or PDF").

Every report is first flattened into one table (columns + rows of text). Quantities stay per unit,
written like "12.5 kg; 30 packages", so a spreadsheet never adds kg and packages together."""

import csv
import io
from datetime import UTC, datetime
from typing import Any

from fpdf import FPDF

from app.domain.formatting import format_quantity


def _qty(items: list[dict[str, Any]]) -> str:
    return "; ".join(format_quantity(i["quantity"], i["unit"]) for i in items) or "-"


def _value(v: Any) -> str:  # noqa: ANN401
    if v is None:
        return "-"
    if isinstance(v, list):
        return _qty(v)
    return str(v)


def table(report: str, data: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    """One header row + data rows for any report."""
    if report == "summary":
        rows = [[k.replace("_", " "), _value(v)] for k, v in data.items()
                if k not in ("filters", "requests_by_status")]
        rows += [[f"requests {status.replace('_', ' ')}", str(n)] for status, n in data["requests_by_status"].items()]
        return ["measure", "value"], rows
    if report == "fairness":
        header = ["receiver category", "receivers", "decided requests", "rejected", "average fill ratio",
                  "quantity received (share)"]
        rows = [[r["name"], str(r["receivers"]), str(r["decided_requests"]), str(r["rejected_requests"]),
                 _value(r["average_fill_ratio"]),
                 "; ".join(f"{format_quantity(q['quantity'], q['unit'])} ({q['share']:.0%})"
                           for q in r["quantity_received"]) or "-"] for r in data["by_receiver_category"]]
        rows += [[f"Gini ({g['unit'].value}, {g['receivers']} receivers)", _value(g["gini"]), "", "", "", ""]
                 for g in data["inequality_by_unit"]]
        rows.append(["override rate", _value(data["override_rate"]), "", "", "", ""])
        return header, rows
    items = data["rows"]
    if not items:
        return ["(no data for these filters)"], []
    keys = [k for k in items[0] if not k.endswith("_id")]  # ids are noise in a printed report
    return [k.replace("_", " ") for k in keys], [[_value(r[k]) for k in keys] for r in items]


def to_csv(header: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    # UTF-8 with a BOM, so Excel shows Urdu names and "→" correctly
    return buffer.getvalue().encode("utf-8-sig")


def _latin1(text: str) -> str:
    """The built-in PDF fonts only know Latin-1; other characters are replaced, never crash the export."""
    return text.replace("→", "->").replace("–", "-").replace("·", "-").encode("latin-1", "replace").decode("latin-1")


def to_pdf(title: str, filters: dict[str, Any], header: list[str], rows: list[list[str]]) -> bytes:
    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 9, _latin1(f"FreshLink - {title}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    used = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in filters.items() if v) or "none (all data)"
    pdf.cell(0, 6, _latin1(f"Filters: {used}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Generated: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    if not rows:
        pdf.cell(0, 8, "No data for these filters.")
        return bytes(pdf.output())
    pdf.set_font("Helvetica", size=8)
    with pdf.table(text_align="LEFT", line_height=5) as tbl:
        heading = tbl.row()
        for h in header:
            heading.cell(_latin1(h.capitalize()))
        for r in rows:
            line = tbl.row()
            for v in r:
                line.cell(_latin1(v))
    return bytes(pdf.output())
