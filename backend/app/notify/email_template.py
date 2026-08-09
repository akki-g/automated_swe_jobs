from __future__ import annotations

from html import escape as _esc

from app.db.models import Match, Posting

# Same palette/typography as frontend/src/styles.css — the email is meant to
# feel like it came from the same product as the web app, not a generic
# notification blast. Email clients strip <style> blocks unreliably and
# don't load @import fonts consistently, so every rule here is inlined (the
# one exception being the responsive grid media query below, which has to
# live in a real <style> block in <head> to work at all) and fonts fall back
# to widely-available system faces that approximate the web app's
# Newsreader/Manrope pairing (a serif headline, a plain sans body) rather
# than depending on a web font actually loading.
_FOREST = "#173f32"
_MINT = "#c9f07b"
_PAPER = "#f8f5ed"
_INK = "#17231e"
_MUTED = "#66716b"
_LINE = "#d9d6ca"

# Container width wide enough for two ~300px card columns plus gutter — see
# the responsive grid below. Job cards render 2-up above _BREAKPOINT so a
# desktop reader sees roughly half as many rows as a single-column list
# would take; the media query collapses back to 1-up below it, so a phone
# gets full-width cards instead of two cramped, unreadable half-width ones.
_CONTAINER_WIDTH = 640
_BREAKPOINT = 600


def _first_name(name: str) -> str:
    return (name or "there").split(" ")[0]


def _field_label(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("_", " ").title()


def _card_html(match: Match, posting: Posting) -> str:
    company = _esc(posting.company)
    title = _esc(posting.title)
    location = _esc(posting.location) if posting.location else None
    blurb = _esc(match.blurb) if match.blurb else None
    url = _esc(posting.url)
    field = _field_label(getattr(match, "matched_target_field", None))

    pills = []
    if match.priority == "high":
        pills.append(
            f'<span style="display:inline-block;background:{_MINT};color:{_FOREST};'
            f'font:600 9px monospace;text-transform:uppercase;letter-spacing:.06em;'
            f'padding:3px 7px;border-radius:6px;margin:0 5px 5px 0;">High fit</span>'
        )
    if field:
        pills.append(
            f'<span style="display:inline-block;background:#edf2e8;color:#3c594e;'
            f'font:600 9px monospace;text-transform:uppercase;letter-spacing:.06em;'
            f'padding:3px 7px;border-radius:6px;margin:0 5px 5px 0;">{_esc(field)}</span>'
        )
    pills_html = "".join(pills)

    meta = company + (f" &middot; {location}" if location else "")
    # line-clamp keeps a long blurb from making one card taller than its
    # row partner in the 2-column grid; clients that don't support it
    # (most non-WebKit ones) just show the full text, which degrades fine.
    blurb_html = (
        f'<p style="margin:8px 0 0;color:{_MUTED};font-size:12px;line-height:1.5;'
        f'display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;'
        f'overflow:hidden;">{blurb}</p>'
        if blurb
        else ""
    )

    return f"""
        <td class="job-cell" valign="top" width="50%" style="width:50%;padding:0 7px 14px 0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border:1px solid {_LINE};border-radius:14px;height:100%;">
            <tr><td style="padding:16px 18px;">
              {f'<div style="margin-bottom:6px;">{pills_html}</div>' if pills_html else ""}
              <div style="font-size:14px;font-weight:700;color:{_INK};line-height:1.3;">{title}</div>
              <div style="margin-top:4px;color:{_MUTED};font-size:11px;">{meta}</div>
              {blurb_html}
              <div style="margin-top:12px;">
                <a href="{url}" style="display:inline-block;background:{_FOREST};color:#ffffff;
                   text-decoration:none;font-size:11px;font-weight:700;padding:8px 13px;
                   border-radius:8px;">View &amp; apply</a>
              </div>
            </td></tr>
          </table>
        </td>"""


def _card_rows_html(items: list[tuple[Match, Posting]]) -> str:
    """Two cards per <tr>, filling the second cell with an empty spacer of
    matching width when there's an odd one out — an unpaired 50%-width <td>
    would otherwise stretch to fill the row on some clients."""
    rows = []
    for i in range(0, len(items), 2):
        pair = items[i : i + 2]
        cells = "".join(_card_html(match, posting) for match, posting in pair)
        if len(pair) == 1:
            cells += '<td class="job-cell" width="50%" style="width:50%;padding:0 0 14px 7px;"></td>'
        rows.append(f"<tr>{cells}</tr>")
    return "".join(rows)


def _section_html(heading: str, subhead: str, items: list[tuple[Match, Posting]]) -> str:
    if not items:
        return ""
    return f"""
        <p style="font:600 18px Georgia,'Times New Roman',serif;margin:26px 0 2px;color:{_INK};">{_esc(heading)}</p>
        <p style="color:{_MUTED};font-size:12px;margin:0 0 12px;">{_esc(subhead)}</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          {_card_rows_html(items)}
        </table>"""


def _section_text(heading: str, items: list[tuple[Match, Posting]]) -> str:
    if not items:
        return ""
    lines = [f"- {posting.company}: {posting.title}\n  {posting.url}" for _, posting in items]
    return f"{heading}:\n\n" + "\n\n".join(lines)


# @media support in email is inconsistent (Outlook desktop's Word engine
# ignores it entirely) but every major client that actually matters for a
# personal tool like this — Gmail (web/app), Apple Mail, iOS/Android mail
# apps, Outlook.com/mobile — honors it, so a narrow-viewport reader still
# gets full-width, readable cards; Outlook desktop just keeps the 2-column
# layout, which is a graceful-enough degradation for a card that isn't the
# empty-state row.
_RESPONSIVE_STYLE = f"""
    <style>
      @media screen and (max-width: {_BREAKPOINT}px) {{
        .job-cell {{ display: block !important; width: 100% !important; padding: 0 0 12px !important; }}
      }}
    </style>"""


def render_digest_email(
    user_name: str,
    just_dropped: list[tuple[Match, Posting]],
    for_you: list[tuple[Match, Posting]],
    matches_url: str,
) -> tuple[str, str]:
    """Returns (plain_text, html) for a digest email, split into two
    sections (see spec addendum: email digest sections):

    - "Just Dropped" — matches never included in an email before. Once
      shown here, the caller marks them delivered so they can never appear
      in this section (or be re-picked at all) again.
    - "For You" — matches an earlier email already showed, still open and
      worth another look; fills out the rest of the digest on quieter days
      instead of sending a half-empty email.

    Both parts are sent together (see ResendEmailProvider.send) — the
    plain-text part is the deliverability-friendly fallback, the HTML part
    is what most inboxes actually render and is styled to match the web app
    rather than read as a bare, easy-to-mistake-for-spam list of links.
    """
    name = _first_name(user_name)
    total = len(just_dropped) + len(for_you)

    if total == 0:
        text = (
            f"Hi {name},\n\n"
            "No new job matches today. We're still scanning throughout the day and "
            "will keep your profile active.\n\n"
            f"View all your matches: {matches_url}"
        )
        html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Signal</title></head>
<body style="margin:0;padding:0;background:{_PAPER};">
        <div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:{_INK};
                    background:{_PAPER};padding:32px 16px;">
          <div style="max-width:{_CONTAINER_WIDTH}px;margin:0 auto;">
            <p style="font-size:15px;">Hi {_esc(name)},</p>
            <p style="color:{_MUTED};font-size:14px;line-height:1.6;">
              No new job matches today. We're still scanning throughout the day and will
              keep your profile active.
            </p>
            <a href="{_esc(matches_url)}" style="display:inline-block;margin-top:8px;
               background:{_FOREST};color:#ffffff;text-decoration:none;font-size:13px;
               font-weight:700;padding:12px 18px;border-radius:10px;">View all your matches</a>
          </div>
        </div>
</body></html>"""
        return text, html

    text = (
        f"Hi {name},\n\n"
        + "\n\n".join(
            part
            for part in (
                _section_text("Just dropped", just_dropped),
                _section_text("For you", for_you),
            )
            if part
        )
        + f"\n\nView all your matches: {matches_url}"
    )

    sections_html = "".join(
        [
            _section_html("Just Dropped", "New since your last email.", just_dropped),
            _section_html("For You", "Still open, still worth a look.", for_you),
        ]
    )

    # The brand lockup below is a table rather than a flex row: Outlook
    # renders with Word's engine, which ignores display:flex and gap, so a
    # flex lockup drops the mark and the wordmark onto separate lines there.
    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Signal</title>
{_RESPONSIVE_STYLE}
</head>
<body style="margin:0;padding:0;background:{_PAPER};">
    <div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:{_INK};
                background:{_PAPER};padding:32px 16px;">
      <div style="max-width:{_CONTAINER_WIDTH}px;margin:0 auto;">
        <table role="presentation" cellpadding="0" cellspacing="0" style="margin-bottom:22px;">
          <tr>
            <td style="width:26px;height:26px;border-radius:8px;background:{_MINT};
                       color:{_FOREST};text-align:center;font-weight:700;font-size:13px;
                       line-height:26px;">&#10022;</td>
            <td style="padding-left:8px;font-weight:700;font-size:15px;color:{_INK};">Job Signal</td>
          </tr>
        </table>
        <p style="font:600 26px Georgia,'Times New Roman',serif;margin:0 0 6px;">
          Hi {_esc(name)}, here's what's new.
        </p>
        <p style="color:{_MUTED};font-size:13px;margin:0 0 4px;">
          {total} match{"es" if total != 1 else ""} picked for you today.
        </p>
        {sections_html}
        <div style="text-align:center;margin:26px 0 8px;">
          <a href="{_esc(matches_url)}" style="display:inline-block;background:{_FOREST};
             color:#ffffff;text-decoration:none;font-size:13px;font-weight:700;
             padding:13px 20px;border-radius:10px;">View all your matches</a>
        </div>
        <p style="color:{_MUTED};font-size:11px;text-align:center;margin-top:24px;">
          You're getting this because you set up a job search profile at Job Signal.
          Manage your settings or pause anytime from your dashboard.
        </p>
      </div>
    </div>
</body>
</html>"""
    return text, html
