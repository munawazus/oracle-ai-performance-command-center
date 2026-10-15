"""CSV and PDF serialization of a persisted Report. Both operate purely in memory - bytes
are streamed straight into the HTTP response body, never written to a filesystem path, so
there is no user-controlled filename and no path-traversal surface to defend (master plan
Sec 21 export requirements). Free text pulled from the database (incident titles,
resolution notes, recipient addresses) is XML-escaped before it reaches reportlab's
Paragraph markup parser, since that markup is a real (if small) injection surface.
"""

import csv
import io
# Escaping outbound text for embedding in reportlab markup, not parsing untrusted XML -
# the XML-attack surface bandit's B406 warns about does not apply to this usage.
from xml.sax.saxutils import escape as xml_escape  # nosec B406

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.platypus import Table as RLTable
from reportlab.platypus import TableStyle

from app.models.report import (
    REPORT_TYPE_CAPACITY,
    REPORT_TYPE_DAILY_DBA,
    REPORT_TYPE_HEALTH,
    REPORT_TYPE_INCIDENT,
    REPORT_TYPE_PERFORMANCE,
    REPORT_TYPE_SQL_REGRESSION,
    Report,
)

SUPPORTED_FORMATS = ("csv", "pdf")


def _iso_or_dash(value) -> str:
    return value.isoformat() if value else "-"


def to_csv(report: Report) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(["report_id", report.id])
    writer.writerow(["report_type", report.report_type])
    writer.writerow(["generated_at", report.generated_at.isoformat()])
    writer.writerow(["timezone", report.timezone])
    writer.writerow(["database", report.database_name_snapshot or ""])
    writer.writerow(["incident", report.incident_title_snapshot or ""])
    writer.writerow(["period_start", _iso_or_dash(report.period_start)])
    writer.writerow(["period_end", _iso_or_dash(report.period_end)])
    writer.writerow(["data_available", report.data_available])
    writer.writerow(["data_completeness_note", report.data_completeness_note])
    for limitation in report.limitations:
        writer.writerow(["limitation", limitation])
    writer.writerow([])

    if not report.data_available:
        writer.writerow(["This report type is not available for this platform yet - see data_completeness_note above."])
        return buf.getvalue().encode("utf-8")

    content = report.content

    if report.report_type == REPORT_TYPE_DAILY_DBA:
        writer.writerow(["-- Active incidents --"])
        writer.writerow(["id", "title", "severity", "status", "opened_at"])
        for i in content["active_incidents"]["items"]:
            writer.writerow([i["id"], i["title"], i["severity"], i["status"], i["opened_at"]])
        writer.writerow([])
        writer.writerow(["-- Anomalies --"])
        writer.writerow(["id", "metric_key", "severity", "detected_at", "observed_value", "baseline_mean"])
        for a in content["important_anomalies"]["top"]:
            writer.writerow([a["id"], a["metric_key"], a["severity"], a["detected_at"], a["observed_value"], a["baseline_mean"]])
        writer.writerow([])
        writer.writerow(["-- Wait classes --"])
        writer.writerow(["wait_class", "centisec_waited_per_sec"])
        for wc in content["wait_summary"].get("wait_classes", []):
            writer.writerow([wc["wait_class"], wc["centisec_waited_per_sec"]])
        writer.writerow([])
        writer.writerow(["-- Blocking chains --"])
        writer.writerow(["blocked_sid", "blocker_sid", "blocker_in_this_sample"])
        for c in content["blocking_summary"].get("blocking_chains", []):
            writer.writerow([c["blocked_sid"], c["blocker_sid"], c["blocker_in_this_sample"]])
        writer.writerow([])
        writer.writerow(["-- Health checks --"])
        writer.writerow(["category", "status", "detail"])
        for c in content["health_check_results"]["checks"]:
            writer.writerow([c["category"], c["status"], c["detail"]])
        writer.writerow([])
        writer.writerow(["-- Recommended investigation items --"])
        for item in content["recommended_investigation_items"]:
            writer.writerow([item])

    elif report.report_type == REPORT_TYPE_INCIDENT:
        writer.writerow(["incident_title", content["incident"]["title"]])
        writer.writerow(["resolution_note", content["incident"].get("resolution_note") or ""])
        writer.writerow([])
        writer.writerow(["-- Timeline --"])
        writer.writerow(["occurred_at", "event_type"])
        for e in content["timeline"]:
            writer.writerow([e["occurred_at"], e["event_type"]])
        writer.writerow([])
        writer.writerow(["-- Evidence (anomalies) --"])
        writer.writerow(["anomaly_id", "metric_key", "severity", "detected_at"])
        for e in content["evidence"]:
            writer.writerow([e["anomaly_id"], e["metric_key"], e["severity"], e["detected_at"]])
        writer.writerow([])
        writer.writerow(["-- Notification history --"])
        writer.writerow(["severity", "status", "attempt_count", "sent_at"])
        for n in content["notification_history"]:
            writer.writerow([n["severity"], n["status"], n["attempt_count"], n["sent_at"] or ""])

    elif report.report_type == REPORT_TYPE_PERFORMANCE:
        writer.writerow(["-- Active session count timeseries --"])
        writer.writerow(["sampled_at", "active_session_count"])
        for p in content["historical_comparison"]["active_session_count_timeseries"]:
            writer.writerow([p["sampled_at"], p["active_session_count"]])
        writer.writerow([])
        writer.writerow(["-- Anomalies --"])
        writer.writerow(["metric_key", "severity", "detected_at"])
        for a in content["anomalies"]:
            writer.writerow([a["metric_key"], a["severity"], a["detected_at"]])

    elif report.report_type == REPORT_TYPE_HEALTH:
        writer.writerow(["-- Health checks --"])
        writer.writerow(["category", "status", "detail"])
        for c in content["categories"]:
            writer.writerow([c["category"], c["status"], c["detail"]])

    elif report.report_type in (REPORT_TYPE_SQL_REGRESSION, REPORT_TYPE_CAPACITY):
        pass  # header block above already states unavailability - nothing tabular to add

    return buf.getvalue().encode("utf-8")


def _p(text, style) -> Paragraph:
    return Paragraph(xml_escape(str(text)), style)


def _table(rows: list[list[str]], styles) -> RLTable:
    data = [[_p(cell, styles["Normal"]) for cell in row] for row in rows]
    t = RLTable(data, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return t


def to_pdf(report: Report) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, title=f"{report.report_type} report {report.id}")
    styles = getSampleStyleSheet()
    story: list = []

    story.append(_p(f"{report.report_type.replace('_', ' ').title()} Report", styles["Title"]))
    story.append(Spacer(1, 12))

    story.append(
        _table(
            [
                ["Field", "Value"],
                ["Report ID", report.id],
                ["Generated at", f"{report.generated_at.isoformat()} ({report.timezone})"],
                ["Database", report.database_name_snapshot or "-"],
                ["Incident", report.incident_title_snapshot or "-"],
                ["Period start", _iso_or_dash(report.period_start)],
                ["Period end", _iso_or_dash(report.period_end)],
                ["Data available", str(report.data_available)],
            ],
            styles,
        )
    )
    story.append(Spacer(1, 12))
    story.append(_p("Data completeness", styles["Heading3"]))
    story.append(_p(report.data_completeness_note, styles["Normal"]))
    if report.limitations:
        story.append(Spacer(1, 6))
        story.append(_p("Limitations", styles["Heading3"]))
        for lim in report.limitations:
            story.append(_p(f"• {lim}", styles["Normal"]))
    story.append(Spacer(1, 12))

    if not report.data_available:
        story.append(_p("This report type is not available for this platform yet.", styles["Normal"]))
        doc.build(story)
        return buf.getvalue()

    content = report.content

    if report.report_type == REPORT_TYPE_DAILY_DBA:
        story.append(_p("Active incidents", styles["Heading3"]))
        rows = [["Title", "Severity", "Status", "Opened"]] + [
            [i["title"], i["severity"], i["status"], i["opened_at"]] for i in content["active_incidents"]["items"]
        ]
        story.append(_table(rows, styles) if len(rows) > 1 else _p("None open.", styles["Normal"]))
        story.append(Spacer(1, 10))

        story.append(_p("Health checks", styles["Heading3"]))
        rows = [["Category", "Status", "Detail"]] + [
            [c["category"], c["status"], c["detail"]] for c in content["health_check_results"]["checks"]
        ]
        story.append(_table(rows, styles))
        story.append(Spacer(1, 10))

        story.append(_p("Recommended investigation items", styles["Heading3"]))
        for item in content["recommended_investigation_items"]:
            story.append(_p(f"• {item}", styles["Normal"]))

    elif report.report_type == REPORT_TYPE_INCIDENT:
        incident = content["incident"]
        story.append(_p(incident["title"], styles["Heading2"]))
        if incident.get("resolution_note"):
            story.append(_p("Resolution note", styles["Heading3"]))
            story.append(_p(incident["resolution_note"], styles["Normal"]))
            story.append(Spacer(1, 10))

        story.append(_p("Timeline", styles["Heading3"]))
        rows = [["Occurred at", "Event"]] + [[e["occurred_at"], e["event_type"]] for e in content["timeline"]]
        story.append(_table(rows, styles) if len(rows) > 1 else _p("No timeline events.", styles["Normal"]))
        story.append(Spacer(1, 10))

        story.append(_p("Notification history", styles["Heading3"]))
        rows = [["Severity", "Status", "Attempts", "Sent at"]] + [
            [n["severity"], n["status"], str(n["attempt_count"]), n["sent_at"] or "-"]
            for n in content["notification_history"]
        ]
        story.append(_table(rows, styles) if len(rows) > 1 else _p("No notifications were queued.", styles["Normal"]))

    elif report.report_type == REPORT_TYPE_PERFORMANCE:
        story.append(_p("Anomalies in period", styles["Heading3"]))
        rows = [["Metric", "Severity", "Detected at"]] + [
            [a["metric_key"], a["severity"], a["detected_at"]] for a in content["anomalies"]
        ]
        story.append(_table(rows, styles) if len(rows) > 1 else _p("No anomalies in this period.", styles["Normal"]))

    elif report.report_type == REPORT_TYPE_HEALTH:
        story.append(_p("Health checks", styles["Heading3"]))
        rows = [["Category", "Status", "Detail"]] + [[c["category"], c["status"], c["detail"]] for c in content["categories"]]
        story.append(_table(rows, styles))

    doc.build(story)
    return buf.getvalue()


def export_report(report: Report, fmt: str) -> bytes:
    if fmt == "csv":
        return to_csv(report)
    if fmt == "pdf":
        return to_pdf(report)
    raise ValueError(f"unsupported export format: {fmt}")
