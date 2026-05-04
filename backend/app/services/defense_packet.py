"""Commercial Defense Packet renderer.

Produces a markdown (and HTML) "Defense Packet" per :class:`DelayClaim`.
The packet has four sections; the **Statement of Facts** (section 2) is the
narrative + evidentiary log that fuses the timestamped message trail with
the measured idle cost, and is what gets persisted on the claim row so it
is reproducible at filing time.

Sections::

    1. COVER LETTER         — addressed to the causing party
    2. STATEMENT OF FACTS   — narrative + audit trail (persisted)
    3. FINANCIAL DAMAGES    — table; values masked per CFO policy
    4. SCHEDULE IMPACT      — CPM macro-impact (days COD slipped)

PDF / DOCX export are deliberate stubs — wire `reportlab` / `python-docx`
when the legal team's letterhead is finalized; the markdown below already
carries everything those formats need.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.rbac import FinancialField, TechnicalRole, VisibilityPolicy
from app.models.financial import Project
from app.models.risk import DelayClaim, IdleEvent, RFCDrawing, PermitStatus


MASKED = "— masked —"


@dataclass
class PacketContext:
    claim: DelayClaim
    project: Project
    idle_event: IdleEvent
    rfc: RFCDrawing | None
    permit: PermitStatus | None
    messages: list[dict]                  # already serialized (ts, from, org, body)
    viewer_role: TechnicalRole
    policy: VisibilityPolicy


# ---------------------------------------------------------------------------
# Statement of Facts — the core template
# ---------------------------------------------------------------------------

_SOF_TEMPLATE = """\
## 2. STATEMENT OF FACTS

**Project:** {project_code} — {project_name}
**Causing party:** {causing_org}
**Subject:** {subject_label}
**Idle event #{idle_id} opened:** {idle_started}

### 2.1 Underlying engineering deliverable

{deliverable_paragraph}

### 2.2 Field consequences

Beginning **{idle_started}**, the following resources were rendered idle
awaiting the deliverable identified above:

- Crew on standby: **{idle_crew}** personnel
- Equipment on standby: {equipment_list}
- Total measured idle time: **{idle_hours:.1f} hours** (≈ {impact_days:.1f} working days)

### 2.3 Communication trail (timestamped)

The following inter-company communications, contemporaneously tagged to the
subject artifact within the EPC Master-Wrap platform, form the evidentiary
record of notice and demand:

{message_log}

### 2.4 Direct financial damages

The measured Field Idle Cost arising from §2.2 above totals
**{total_cost}** (see §3 for itemisation). This amount represents
recoverable cost incurred by the Lead EPC by reason of the failure
described in §2.1.
"""

_DELIVERABLE_RFC = """\
Drawing **{drawing_no}** "{title}" (discipline: {discipline}) was
contractually due for "Released for Construction" issuance by
**{issuer_org}** on **{rfc_due}**. As of the opening of idle event
#{idle_id} ({idle_started}), the drawing had not been issued."""

_DELIVERABLE_PERMIT = """\
Permit **{permit_type}** issued by **{authority}** was scheduled for
grant on **{target_date}**. As of the opening of idle event #{idle_id}
({idle_started}), the permit had not been granted (status:
**{status}**)."""

_NO_MESSAGES = "_No tagged communications were found for the subject artifact._"


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "n/a"
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.strftime("%Y-%m-%d %H:%M UTC")


def _money(value: float | None, allowed: bool) -> str:
    if not allowed:
        return MASKED
    return f"${value:,.0f}" if value is not None else "n/a"


def _render_message_log(messages: list[dict]) -> str:
    if not messages:
        return _NO_MESSAGES
    lines = []
    for m in messages:
        ts = m.get("ts", "?")
        sender = m.get("from", "?")
        org = m.get("org", "")
        body = (m.get("body") or "").replace("\n", " ").strip()
        lines.append(f"- **{ts}** — `{sender}` ({org}) — {body}")
    return "\n".join(lines)


def render_statement_of_facts(ctx: PacketContext) -> str:
    """Produce the persisted §2 narrative.

    This is the function the user asked to see. It deterministically
    combines:
      • the RFC (or permit) metadata,
      • the IdleEvent measurements (crew, equipment, hours),
      • the harvested message trail,
      • the masked financial total,
    into a single markdown block stored on ``DelayClaim.statement_of_facts``.
    """
    evt = ctx.idle_event
    project = ctx.project

    if ctx.rfc is not None:
        deliverable = _DELIVERABLE_RFC.format(
            drawing_no=ctx.rfc.drawing_no,
            title=ctx.rfc.title,
            discipline=ctx.rfc.discipline,
            issuer_org=ctx.rfc.issuer_org,
            rfc_due=_fmt_dt(ctx.rfc.rfc_due),
            idle_id=evt.id,
            idle_started=_fmt_dt(evt.started_at),
        )
        subject_label = f"RFC drawing **{ctx.rfc.drawing_no}** — {ctx.rfc.title}"
    elif ctx.permit is not None:
        deliverable = _DELIVERABLE_PERMIT.format(
            permit_type=ctx.permit.permit_type,
            authority=ctx.permit.authority,
            target_date=_fmt_dt(ctx.permit.target_date),
            idle_id=evt.id,
            idle_started=_fmt_dt(evt.started_at),
            status=ctx.permit.status,
        )
        subject_label = f"Permit **{ctx.permit.permit_type}** — {ctx.permit.authority}"
    else:
        deliverable = "_Subject artifact metadata unavailable._"
        subject_label = "—"

    equipment_list = ", ".join(evt.idle_equipment) if evt.idle_equipment else "none recorded"

    cost_allowed = FinancialField.FIELD_IDLE_COST in ctx.policy.fields_for(ctx.viewer_role)
    total_cost = _money(float(evt.computed_cost or 0), cost_allowed)

    impact_days = ctx.claim.impact_days or 0.0
    idle_hours = impact_days * 10.0  # mirrors WORKING_HOURS_PER_DAY in field_idle_cost

    return _SOF_TEMPLATE.format(
        project_code=project.code,
        project_name=project.name,
        causing_org=ctx.claim.causing_org,
        subject_label=subject_label,
        idle_id=evt.id,
        idle_started=_fmt_dt(evt.started_at),
        deliverable_paragraph=deliverable,
        idle_crew=evt.idle_crew,
        equipment_list=equipment_list,
        idle_hours=idle_hours,
        impact_days=impact_days,
        message_log=_render_message_log(ctx.messages),
        total_cost=total_cost,
    )


# ---------------------------------------------------------------------------
# Cover letter, financials, schedule impact, full packet
# ---------------------------------------------------------------------------

_COVER_LETTER = """\
## 1. COVER LETTER

**To:** {causing_org}
**From:** Lead EPC — Project {project_code}
**Date:** {today}
**Re:** Notice of Recoverable Cost — Idle Event #{idle_id} ({subject_ref})

This packet is issued under the EPC contract's "wrap responsibility"
provisions. It documents the failure of {causing_org} to meet its
contractual obligations with respect to {subject_ref}, the resulting
field idleness incurred by the Lead EPC, the timestamped record of
notice and demand, and the calculated direct financial damages. The
Lead EPC reserves all rights and remedies and demands prompt response
within the period stipulated by the master agreement.
"""

_DAMAGES_TEMPLATE = """\
## 3. FINANCIAL DAMAGES

| Component                | Amount        |
|--------------------------|---------------|
| Crew idle cost           | {crew}        |
| Equipment idle cost      | {equip}       |
| **Total recoverable**    | **{total}**   |

CPM macro-impact (§4) is documented separately; consequential damages
arising from COD slippage are reserved.
"""

_SCHEDULE_TEMPLATE = """\
## 4. SCHEDULE IMPACT (CPM Macro-Impact)

The Critical Path recompute triggered by this idle event projects the
finish date as **{cod_after}**, a slip of **{cod_shift_days:.1f} days**
versus the contractual COD target of **{cod_target}**.
"""

_FOOTER = "\n— Drafted by EPC Master-Wrap Agent · Claim #{claim_id} · status: {status}\n"


def render_packet_markdown(ctx: PacketContext) -> str:
    cost_allowed = FinancialField.FIELD_IDLE_COST in ctx.policy.fields_for(ctx.viewer_role)
    evt = ctx.idle_event

    impact_days = ctx.claim.impact_days or 0.0
    idle_hours = impact_days * 10.0
    crew_cost = idle_hours * evt.idle_crew * float(evt.crew_burdened_rate or 0)
    equip_cost = max(0.0, float(evt.computed_cost or 0) - crew_cost)
    total = float(evt.computed_cost or 0)

    sof = ctx.claim.statement_of_facts or render_statement_of_facts(ctx)
    cover = _COVER_LETTER.format(
        causing_org=ctx.claim.causing_org,
        project_code=ctx.project.code,
        today=_fmt_dt(datetime.now(timezone.utc)),
        idle_id=evt.id,
        subject_ref=ctx.claim.subject_ref or "subject artifact",
    )
    damages = _DAMAGES_TEMPLATE.format(
        crew=_money(crew_cost, cost_allowed),
        equip=_money(equip_cost, cost_allowed),
        total=_money(total, cost_allowed),
    )
    cod_target = _fmt_dt(ctx.project.cod_target)
    cod_after = _fmt_dt(ctx.project.cod_target) if ctx.claim.cod_shift_days == 0 else "see §4"
    schedule = _SCHEDULE_TEMPLATE.format(
        cod_after=cod_after,
        cod_shift_days=ctx.claim.cod_shift_days,
        cod_target=cod_target,
    )
    footer = _FOOTER.format(claim_id=ctx.claim.id, status=ctx.claim.status)

    return "\n".join([cover, sof, damages, schedule, footer])


def render_packet_html(ctx: PacketContext) -> str:
    """Print-to-PDF friendly HTML; minimal styling so corp letterhead overrides cleanly."""
    md = render_packet_markdown(ctx)
    body = (
        md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Defense Packet</title>"
        "<style>body{font-family:Georgia,serif;max-width:780px;margin:40px auto;line-height:1.5}"
        "pre{white-space:pre-wrap;font-family:inherit}</style></head>"
        f"<body><pre>{body}</pre></body></html>"
    )
