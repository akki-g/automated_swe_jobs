from __future__ import annotations

from html import escape as _esc

from app.db.models import Match, Posting

# Same palette/typography as frontend/src/styles.css — the email is meant to
# feel like it came from the same product as the web app, not a generic
# notification blast. Email clients strip <style> blocks unreliably and
# don't load @import fonts consistently, so every rule here is inlined and
# fonts fall back to widely-available system faces that approximate the
# web app's Newsreader/Manrope pairing (a serif headline, a plain sans body)
# rather than depending on a web font actually loading.
_FOREST = "#173f32"
_MINT = "#c9f07b"
_PAPER = "#f8f5ed"
_INK = "#17231e"
_MUTED = "#66716b"
_LINE = "#d9d6ca"


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
            f'font:600 10px monospace;text-transform:uppercase;letter-spacing:.06em;'
            f'padding:4px 8px;border-radius:6px;margin:0 6px 6px 0;">High fit</span>'
        )
    if field:
        pills.append(
            f'<span style="display:inline-block;background:#edf2e8;color:#3c594e;'
            f'font:600 10px monospace;text-transform:uppercase;letter-spacing:.06em;'
            f'padding:4px 8px;border-radius:6px;margin:0 6px 6px 0;">{_esc(field)}</span>'
        )
    pills_html = "".join(pills)

    meta = company + (f" &middot; {location}" if location else "")
    blurb_html = (
        f'<p style="margin:10px 0 0;color:{_MUTED};font-size:13px;line-height:1.55;">{blurb}</p>'
        if blurb
        else ""
    )

    return f"""
    <tr><td style="padding:0 0 14px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border:1px solid {_LINE};border-radius:14px;">
        <tr><td style="padding:20px 22px;">
          {f'<div style="margin-bottom:8px;">{pills_html}</div>' if pills_html else ""}
          <div style="font-size:15px;font-weight:700;color:{_INK};">{title}</div>
          <div style="margin-top:4px;color:{_MUTED};font-size:12px;">{meta}</div>
          {blurb_html}
          <div style="margin-top:14px;">
            <a href="{url}" style="display:inline-block;background:{_FOREST};color:#ffffff;
               text-decoration:none;font-size:12px;font-weight:700;padding:10px 16px;
               border-radius:9px;">View &amp; apply</a>
          </div>
        </td></tr>
      </table>
    </td></tr>
    """


def render_digest_email(
    user_name: str, items: list[tuple[Match, Posting]], matches_url: str
) -> tuple[str, str]:
    """Returns (plain_text, html) for a digest email. Both parts are sent
    together (see ResendEmailProvider.send) — the plain-text part is the
    deliverability-friendly fallback, the HTML part is what most inboxes
    actually render and is styled to match the web app rather than read as
    a bare, easy-to-mistake-for-spam list of links."""
    name = _first_name(user_name)

    if not items:
        text = (
            f"Hi {name},\n\n"
            "No new job matches today. We're still scanning throughout the day and "
            "will keep your profile active.\n\n"
            f"View all your matches: {matches_url}"
        )
        html = f"""
        <div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:{_INK};
                    background:{_PAPER};padding:32px 16px;">
          <div style="max-width:560px;margin:0 auto;">
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
        """
        return text, html

    text_lines = [f"- {posting.company}: {posting.title}\n  {posting.url}" for _, posting in items]
    text = (
        f"Hi {name},\n\n"
        f"New matches for you:\n\n" + "\n\n".join(text_lines) + f"\n\nView all your matches: {matches_url}"
    )

    cards_html = "".join(_card_html(match, posting) for match, posting in items)
    # The brand lockup below is a table rather than a flex row: Outlook
    # renders with Word's engine, which ignores display:flex and gap, so a
    # flex lockup drops the mark and the wordmark onto separate lines there.
    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:{_INK};
                background:{_PAPER};padding:32px 16px;">
      <div style="max-width:560px;margin:0 auto;">
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
        <p style="color:{_MUTED};font-size:13px;margin:0 0 22px;">
          {len(items)} match{"es" if len(items) != 1 else ""} picked for you today.
        </p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          {cards_html}
        </table>
        <div style="text-align:center;margin:22px 0 8px;">
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
    """
    return text, html
