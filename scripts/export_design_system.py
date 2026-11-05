"""Export the owner view's design system as a Claude design system: the files the saved copy in
Claude holds (docs/design-system.md, The saved copy in Claude), rebuilt from this repository.

    make owner-demo                      # in another terminal; the previews are its own markup
    make design-export OUT=/tmp/revin-ds # then publish OUT/project to the saved copy

Tokens come from app/static/owner/tokens.css, the stylesheet from components.css unchanged, each
component's guidelines from its section of docs/design-system.md, and the brand book from
docs/design-system-rules.md. Each preview is captured from the running demo, so it is the app's own
markup. The demo's development account signs in (scripts/seed_owner_day.py).
"""

import argparse
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TITLE = "Revin owner view"
# The saved copy's uploads of the logos in app/static/owner, as (id, stored size). A new copy
# uploads them again and puts what it is given here.
BLOB = {
    "revin-logo.svg": ("d4bade9087fbe779ddf32517c70f4188", 3009),
    "revin-mark.png": ("3946d292f526f32340e9dd63d13658b0", 394),
    "servicetitan-logo.svg": ("e267378af832279d95e5f22e38a08aff", 14278),
}
# Names the stylesheet reads that a Claude design system's tokens.json can't hold: the font stacks
# under their stylesheet names, and motion.
BUNDLE_HEAD = """/* The owner view's components.css, unchanged, after the names it reads that tokens.json can't
   hold: the font stacks under their stylesheet names, and motion. */
:root {
  --font: var(--font-sans);
  --mono: var(--font-mono);
  --ease: cubic-bezier(0.2, 0, 0, 1);
  --fast: 120ms;
  --base: 200ms;
}
@media (prefers-reduced-motion: reduce) {
  :root { --fast: 0ms; --base: 0ms; }
}

"""
ALIASES = {"font", "mono", "ease", "fast", "base"}


def read_values() -> dict:
    """Every custom property tokens.css declares, outside the reduced-motion override."""
    css = (REPO / "app/static/owner/tokens.css").read_text()
    return dict(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", css.split("@media")[0]))


COLOR_USAGE = {
    "page": "The page behind panels, Revin's Offwhite.",
    "surface": "Panels and controls.",
    "sunken": "Board axis, group rows, notes in a call, the player row.",
    "hover": "A row under the pointer.",
    "selected": "A call line picked for a report.",
    "line": "Panel edges and the figures line.",
    "line-soft": "Rows inside a panel.",
    "line-strong": "Control edges.",
    "ink": "Body copy and headings, on surface, sunken and page (17.5:1 on white).",
    "ink-2": "Secondary copy, notes in a call, on surface and sunken.",
    "muted": "Labels, meta lines and inactive tabs, on surface (6.7:1).",
    "faint": "Times, captions and table headings only, on surface (5.2:1) and page (4.8:1).",
    "disabled": "Disabled controls and zero counts. Not for text that must be read.",
    "ember": "The brand: the logo, the active tab underline, the now line, agent nodes in a call. Never a status.",
    "ember-ink": "The agent's name in a call; Ember as text on surface.",
    "ember-action": "The primary button and the now label, where white text sits on Ember (4.7:1). Brand Ember under white is 3.8:1.",
    "ember-wash": "The report box ground.",
    "ember-line": "The report box edge.",
    "urgent": "Urgent dots, rings and bars; safety calls.",
    "urgent-ink": "Text on urgent-wash: Urgent tags, late callbacks, alarm figures.",
    "urgent-wash": "Urgent tags and open urgent rows.",
    "urgent-line": "Urgent pill edges.",
    "high": "High priority dots; things held or waiting. Darkened from Anvil's #FFBE00 so a dot holds up on white.",
    "high-ink": "Text on high-wash.",
    "high-wash": "High tags, held steps, the search match highlight.",
    "high-line": "High pill edges.",
    "done": "Done, sent, answering: dots and check marks.",
    "done-ink": "Text on done-wash; a rise in the Results ledger.",
    "done-wash": "Done and Booked tags.",
    "done-line": "Done pill edges.",
    "info": "Links, booked-job dots, arrival windows, the focus ring's hue.",
    "info-ink": "Text on info-wash; a link under the pointer.",
    "info-wash": "Arrival window bars and info tags.",
    "info-line": "Arrival window edges.",
    "neutral": "Other calls, Normal priority dots, hollow toggle swatches.",
    "neutral-ink": "Text on neutral-wash; neutral tags and counts.",
    "neutral-wash": "The segmented control track, neutral tags, count pills.",
}
SPACE_USAGE = {
    "s-1": "Gap inside a tag or between tags.",
    "s-2": "Gap between controls in a row; padding inside notes.",
    "s-3": "Table cell padding; list row padding.",
    "s-4": "Panel padding.",
    "s-5": "Padding between figures.",
    "s-6": "Space between panels; page top padding.",
    "s-8": "Space between the page title and its figures.",
    "s-10": "Rarely: large separations on the specimen page.",
    "s-12": "Page bottom padding.",
    "gutter": "The page's side padding.",
    "field-min": "The narrowest a field in a form table gets; on a phone the table scrolls sideways.",
    "icon-sm": "A small inline icon: a disclosure's chevron.",
}
RADIUS_USAGE = {
    "r-sm": "Tags and the active segmented button.",
    "r-md": "Controls, notes, windows on the board.",
    "r-lg": "Panels and popovers.",
    "r-full": "Count pills, board marks, status dots, nodes.",
}
SHADOW_USAGE = {
    "shadow-1": "The active segmented button. Panels have none.",
    "shadow-pop": "Anything that floats over the page: suggestions, popovers, tooltips.",
    "focus": "The 3px blue focus ring on every control.",
}
SIZE_USAGE = {
    "control-h": "The height every control in a row shares.",
    "tag-h": "Tags inside tables.",
    "row-h": "A fixed table's row, and a form table's minimum row.",
    "header-h": "The app header.",
    "page-w": "The widest the page grows.",
}
TEXT_USAGE = {
    "text-xs": "Tags, table headings, hour labels.",
    "text-sm": "Controls, captions, notes in a call.",
    "text-md": "Body copy and table rows.",
    "text-lg": "Values in the Results ledger.",
    "text-xl": "The sign-in title.",
    "text-2xl": "Page titles.",
    "text-3xl": "The value in a figure.",
    "leading-tight": "Titles and figure values.",
    "leading": "Body copy.",
    "tracking-tight": "Page titles and figure values.",
}
WEIGHT_USAGE = {
    "regular": "Body copy.",
    "medium": "Tabs, controls, quiet labels.",
    "semibold": "Headings, the active tab, row names, tags.",
    "bold": "Kept for emphasis the page does not use yet.",
}


def family(usage: dict, values: dict) -> dict:
    return {"tokens": [{"name": n, "value": values[n], "usage": u} for n, u in usage.items()]}


def build_tokens(ref: str) -> dict:
    """tokens.json for the saved copy, in the list shape a Claude design system reads."""
    values = read_values()
    color_tokens = [
        {"name": n, "value": values[n].lower(), "usage": u} for n, u in COLOR_USAGE.items()
    ]
    synced = datetime.now(UTC).strftime("%Y-%m-%d")
    tokens = {
        "name": TITLE,
        "version": 1,
        "color": {"themes": [{"id": "light", "name": "Light"}], "tokens": color_tokens},
        "type": {
            "fonts": [
                {
                    "family": "Public Sans",
                    "file": "fonts/PublicSans-latin.woff2",
                    "weight": "100 900",
                    "style": "normal",
                }
            ],
            "families": {
                "sans": '"Public Sans", "Helvetica Neue", Arial, sans-serif',
                "mono": 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
            },
            "groups": [
                {
                    "name": "Display",
                    "family": "sans",
                    "styles": [
                        {
                            "name": "figure-value",
                            "fontSize": "32px",
                            "lineHeight": 1.15,
                            "fontWeight": 600,
                            "letterSpacing": "-0.015em",
                            "sample": "4 of 6",
                            "usage": "The number in a figure, tabular figures on.",
                        },
                        {
                            "name": "page-title",
                            "fontSize": "26px",
                            "lineHeight": 1.25,
                            "fontWeight": 600,
                            "letterSpacing": "-0.015em",
                            "sample": "Tuesday, Nov 3",
                            "usage": "One per page.",
                        },
                        {
                            "name": "sign-in-title",
                            "fontSize": "20px",
                            "lineHeight": 1.25,
                            "fontWeight": 600,
                            "sample": "Owner view",
                            "usage": "The sign-in panel.",
                        },
                    ],
                },
                {
                    "name": "Text",
                    "family": "sans",
                    "styles": [
                        {
                            "name": "ledger-value",
                            "fontSize": "16px",
                            "lineHeight": 1.5,
                            "fontWeight": 600,
                            "sample": "96%",
                            "usage": "Values in the Results ledger.",
                        },
                        {
                            "name": "heading",
                            "fontSize": "14px",
                            "lineHeight": 1.5,
                            "fontWeight": 600,
                            "sample": "Needs attention",
                            "usage": "Panel heads and the name in a row.",
                        },
                        {
                            "name": "body",
                            "fontSize": "14px",
                            "lineHeight": 1.5,
                            "fontWeight": 400,
                            "sample": "Carbon monoxide reported on the call",
                            "usage": "Body copy and table rows.",
                        },
                        {
                            "name": "control",
                            "fontSize": "13px",
                            "lineHeight": 1.5,
                            "fontWeight": 500,
                            "sample": "Show resolved",
                            "usage": "Buttons, toggles, tabs, notes in a call.",
                        },
                        {
                            "name": "caption",
                            "fontSize": "12px",
                            "lineHeight": 1.5,
                            "fontWeight": 400,
                            "sample": "2 urgent",
                            "usage": "Tags, table headings, hour labels, the line under a figure.",
                        },
                    ],
                },
                {
                    "name": "Code",
                    "family": "mono",
                    "styles": [
                        {
                            "name": "tech",
                            "fontSize": "12px",
                            "lineHeight": 1.5,
                            "fontWeight": 400,
                            "sample": "book_appointment ok gate=safety_override",
                            "usage": "Technical details and the log only.",
                        },
                    ],
                },
            ],
        },
        "spacing": family(SPACE_USAGE, values),
        "radius": family(RADIUS_USAGE, values),
        "shadow": family(SHADOW_USAGE, values),
        "size": family(SIZE_USAGE, values),
        "text": family(TEXT_USAGE, values),
        "weight": family(WEIGHT_USAGE, values),
        "meta": {
            "source": "github",
            "repo": "Joconnell04/summit-air-agent",
            "ref": ref,
            "paths": {
                "tokens": ["app/static/owner/tokens.css"],
                "fonts": ["app/static/owner/fonts/PublicSans-latin.woff2"],
                "assets": [
                    "app/static/owner/revin-logo.svg",
                    "app/static/owner/revin-mark.png",
                    "app/static/owner/servicetitan-logo.svg",
                ],
                "docs": ["docs/design-system.md"],
            },
            "components": {
                "stylesheet": "app/static/owner/components.css",
                "specimen": "app/templates/design.html",
            },
            "synced": synced,
        },
    }
    return tokens


def compiled_css(tokens: dict) -> str:
    """The first tokens.css, laid out as the design system's page compiles it."""
    lines = [f"/* {TITLE} \u2014 generated from tokens.json */", ':root, [data-theme="light"] {']
    lines += [f"  --{t['name']}: {t['value']};" for t in tokens["color"]["tokens"]]
    lines += [f"  --{t['name']}: {t['value']};" for t in tokens["shadow"]["tokens"]]
    lines += ["}", ":root {"]
    for fam in ("spacing", "radius", "size", "text", "weight"):
        lines += [f"  --{t['name']}: {t['value']};" for t in tokens[fam]["tokens"]]
    for key, stack in tokens["type"]["families"].items():
        lines.append(f"  --font-{key}: {stack};")
    lines.append("}")
    for g in tokens["type"]["groups"]:
        for s in g["styles"]:
            decl = (
                f"font-family: var(--font-{g['family']}); font-size: {s['fontSize']}; "
                f"line-height: {s['lineHeight']}; font-weight: {s['fontWeight']};"
            )
            if "letterSpacing" in s:
                decl += f" letter-spacing: {s['letterSpacing']};"
            lines.append(f".{s['name']} {{ {decl} }}")
    lines.append(
        '@font-face { font-family: "Public Sans"; src: url("fonts/PublicSans-latin.woff2") '
        'format("woff2"); font-weight: 100 900; font-style: normal; font-display: swap; }'
    )
    return "\n".join(lines) + "\n"


def doc_sections() -> dict:
    """docs/design-system.md's component sections by heading, without a story in brackets."""
    doc = (REPO / "docs/design-system.md").read_text()
    return {
        re.sub(r" \(.*\)$", "", m.group(1)).strip(): m.group(2).strip()
        for m in re.finditer(r"^### (.+?)\n(.*?)(?=^### |^## |\Z)", doc, re.DOTALL | re.MULTILINE)
    }


COVER = """<!-- @dsCard height=288 -->
<!doctype html>
<html lang="en">
<head><meta charset="utf-8">
<style>
  body { margin: 0; background: var(--page); }
  .cover { position: relative; width: 960px; height: 288px; overflow: hidden; background: var(--page); }
  svg { position: absolute; left: 0; top: 0; }
  .ink { fill: var(--ink); }
  .ember { fill: var(--ember); }
  .info { fill: var(--info); }
  .wash { fill: var(--high-wash); }
  .surface { fill: var(--surface); }
  .high { fill: var(--high); }
  .now { fill: var(--ember); }
  .slab { rx: 8px; }
  .pill { rx: 12px; }
  .name { position: absolute; left: 40px; bottom: 40px; max-width: 440px; }
  .name h1 { margin: 0; font-family: var(--font-sans); font-size: 72px; line-height: 0.95; font-weight: 600; letter-spacing: -0.015em; color: var(--ink); }
  .name p { margin: 12px 0 0; font-family: var(--font-sans); font-size: 14px; color: var(--muted); }
</style></head>
<body>
<div class="cover">
  <svg width="960" height="288" viewBox="0 0 960 288" aria-hidden="true">
    <!-- blocks: ink 320x120 bleeding top right, ember 96x216 slab bleeding bottom, info 200x64 lane, high-wash 104x144 bleeding right; radius r-lg on the slab
         arrangement: one tall Ember slab with lanes beside it, the way the dispatch board stacks lanes beside its day
         pattern: a literal motif, the board itself: pills 24px high (rx half the short side) on the ink lane, dots on the info lane, and the 2px now line over all of them
         steps: lanes and pills on s-6 (24px) and s-4 (16px) pitches; radii r-lg and half a pill -->
    <rect class="ink" x="640" y="-8" width="328" height="128"/>
    <rect class="ember slab" x="520" y="72" width="96" height="232"/>
    <rect class="info" x="640" y="144" width="200" height="64"/>
    <rect class="wash" x="856" y="144" width="112" height="152"/>
    <rect class="surface pill" x="664" y="24" width="112" height="24"/>
    <rect class="ember pill" x="792" y="24" width="72" height="24"/>
    <rect class="surface pill" x="712" y="72" width="88" height="24"/>
    <rect class="high pill" x="880" y="72" width="72" height="24"/>
    <circle class="surface" cx="664" cy="176" r="7"/>
    <circle class="surface" cx="712" cy="176" r="7"/>
    <circle class="surface" cx="736" cy="176" r="7"/>
    <circle class="surface" cx="784" cy="176" r="7"/>
    <circle class="high" cx="880" cy="200" r="7"/>
    <circle class="high" cx="920" cy="232" r="7"/>
    <rect class="now" x="823" y="0" width="2" height="288"/>
  </svg>
  <div class="name">
    <h1>Revin owner view</h1>
    <p>The day as a dispatch board, in the words an HVAC owner already uses.</p>
  </div>
</div>
</body>
</html>
"""

LOGOS_README = (
    "Revin's logo goes top left in the app header at 15px high, beside a hairline and the company "
    "name. The mark (a small Ember chevron, PNG) is the favicon only. The ServiceTitan logo appears "
    "only where a number or a job came from ServiceTitan, at 16px high, with the words beside it "
    "never naming ServiceTitan again. Both SVGs are multi-color and keep their own colors; never "
    "recolor or redraw them.\n"
)

# name, card group, card height, page, the element (JS), its sections in docs/design-system.md,
# its classes, and what a page provides
COMPONENTS = [
    (
        "AppHeader",
        "Layout",
        72,
        "/owner",
        "document.querySelector('.app-header')",
        ["App header", "Account"],
        ".app-header .brand .tabs .end",
        "the company name, the current tab, the status and the signed-in account",
    ),
    (
        "PageHead",
        "Layout",
        120,
        "/owner",
        "document.querySelector('.page-head')",
        ["Page head"],
        ".page-head .crumbs .page-title .day-nav",
        "the crumbs, the title and the day",
    ),
    (
        "Figures",
        "Layout",
        130,
        "/owner",
        "document.querySelector('.figures')",
        ["Figures"],
        ".figures .figure .label .value .sub .alarm",
        "up to five labels, values and what each value is made of",
    ),
    (
        "Panel",
        "Layout",
        220,
        "/owner/design",
        "h2('Compact panel').closest('.stack')",
        ["Panel", "Empty state"],
        ".panel .panel-head .panel-body .panel-foot .panel.compact .empty",
        "a title, optional count and controls, and the body",
    ),
    (
        "Button",
        "Controls",
        80,
        "/owner/design",
        "h2('Controls').closest('section').querySelector('.spec-row')",
        ["Buttons"],
        ".btn .btn.primary .btn.quiet .btn.icon",
        "a verb of three words or fewer; at most one primary per page",
    ),
    (
        "Filters",
        "Controls",
        80,
        "/owner/design",
        "h2('Controls').closest('section').querySelector('.filters')",
        ["Filters"],
        ".filters .toggle .seg .select",
        "the choices, their counts and which is on",
    ),
    (
        "Search",
        "Controls",
        80,
        "/owner",
        "document.querySelector('form.search')",
        ["Search"],
        ".search .suggest",
        "the query; matches come from /owner/search/suggest",
    ),
    (
        "Field",
        "Controls",
        220,
        "/owner/design",
        "h2('Fields and actions').closest('section')",
        ["Field", "Form table", "Actions"],
        ".field .field-error .table.form .actions",
        "the label, the value and the server's reason when it is refused",
    ),
    (
        "Disclosure",
        "Controls",
        80,
        "/owner",
        "document.querySelector('#attention .more')",
        ["Disclosure"],
        ".more summary",
        "the count of what it hides",
    ),
    (
        "RangePicker",
        "Controls",
        260,
        "/owner/results?days=7",
        "(()=>{const d=document.querySelector('.range'); d.open=true; return d.closest('.filters')})()",
        ["Range picker"],
        ".range .range-pop",
        "the period, and the from and to dates",
    ),
    (
        "PopoverForm",
        "Controls",
        240,
        "/owner/design",
        "h2('Popover form, basis and outcome tags').closest('section')",
        ["Popover form", "Basis and outcome tags"],
        ".more .popover-form",
        "the question, a hint and one action",
    ),
    (
        "Tag",
        "Status",
        150,
        "/owner/design",
        "h2('Tags').closest('section')",
        ["Tags", "Count"],
        ".tag .tag.urgent .tag.high .tag.done .tag.info .tag.outline .count",
        "one word that names the state",
    ),
    (
        "StatusLine",
        "Status",
        130,
        "/owner/design",
        "h2('Status line').closest('section')",
        ["Status line"],
        ".status .status.ok .status.down",
        "the state from the voice workers' heartbeats and the last call's time",
    ),
    (
        "Tooltip",
        "Status",
        110,
        None,
        None,
        ["Tooltip"],
        ".tip-i .tooltip",
        "the longer detail, as data-tip",
    ),
    (
        "Table",
        "Data",
        420,
        "/owner",
        "document.querySelector('#attention')",
        ["Table"],
        ".table .what .why .time .fit .go tr[data-href] tr.urgent-open tr.closed",
        "rows, each with the call it opens",
    ),
    (
        "JobsTable",
        "Data",
        520,
        "/owner",
        "document.querySelector('table.jobs').closest('.panel')",
        ["Table"],
        ".table.fixed .jobs .tip-i .grip",
        "the jobs, with the reason for each priority",
    ),
    (
        "Tray",
        "Data",
        520,
        "/owner",
        "document.querySelector('[data-tray]')",
        ["Tray", "List"],
        ".tray .tray-tabs .tray-panel .list li.late",
        "the callbacks, leads and calls turned away",
    ),
    (
        "KeyValue",
        "Data",
        260,
        "/owner/calls/c19",
        "document.querySelector('aside .panel.compact:nth-of-type(2)') || document.querySelector('dl.kv').closest('.panel')",
        ["Key-value list"],
        ".kv dt dd",
        "label and value pairs",
    ),
    (
        "Ledger",
        "Data",
        420,
        "/owner/results?days=7",
        "[...document.querySelectorAll('h2')].find(h=>h.textContent.trim()=='Performance').closest('.panel')",
        ["Results ledger"],
        ".table.ledger .value .prev .delta.up .delta.down .delta.flat",
        "each measure for the period and the one before",
    ),
    (
        "BarChart",
        "Data",
        360,
        "/owner/results?days=7",
        "document.querySelector('[data-hours-chart]')",
        ["Bar chart"],
        ".bars .bar .pile .seg-booked .seg-safety .seg-other .legend",
        "calls by hour, and the call type filter",
    ),
    (
        "DayBoard",
        "Board",
        460,
        "/owner",
        "document.querySelector('#board')",
        ["Dispatch board"],
        ".board .board-row .track .mark .window .now .now-label",
        "the day's flags, jobs, other calls and arrival windows, placed by minute",
    ),
    (
        "CallHeader",
        "Call",
        130,
        "/owner/calls/c19",
        "document.querySelector('.call-head')",
        ["Call header"],
        ".call-head .who .facts .step-nav",
        "the caller, one status tag, the facts and the calls either side",
    ),
    (
        "Conversation",
        "Call",
        640,
        "/owner/calls/c19",
        "document.querySelector('.conv-panel')",
        ["Conversation"],
        ".conv .turn .step .rail .note .tech-block .tech-only",
        "the turns and the plain notes from plain.py",
    ),
    (
        "Player",
        "Call",
        90,
        "/owner/design",
        "document.querySelector('.player')",
        ["Player"],
        ".player",
        "the recording's address",
    ),
    (
        "ReportBox",
        "Call",
        140,
        "/owner/calls/c19",
        "document.querySelector('#report')",
        ["Report box"],
        ".report .report-head .report-fields",
        "the call's lines, for the which-line select",
    ),
    (
        "NodeGraph",
        "Bench",
        420,
        "/owner/design",
        "h2('Node graph').closest('section')",
        ["Node graph"],
        ".ng and its detail panel",
        "a call's steps from app/nodes.py",
    ),
    (
        "RateChart",
        "Bench",
        220,
        "/owner/design",
        "h2('Rate chart').closest('section')",
        ["Rate chart"],
        ".rate-chart",
        "eval runs and the ratchet's floor",
    ),
]

PREP = """(sel) => {
  window.h2 = (t) => [...document.querySelectorAll('h2')].find(h => h.textContent.trim() === t);
  const el = eval(sel);
  if (!el) return null;
  const node = el.cloneNode(true);
  node.querySelectorAll('script, .tooltip, audio').forEach(n => n.remove());
  node.querySelectorAll('[href]').forEach(a => a.setAttribute('href', '#'));
  node.querySelectorAll('[action]').forEach(f => f.removeAttribute('action'));
  node.querySelectorAll('[id]').forEach(n => n.removeAttribute('id'));
  node.querySelectorAll('label[for]').forEach(n => n.removeAttribute('for'));
  return {html: node.outerHTML, width: Math.ceil(el.getBoundingClientRect().width)};
}"""

TOOLTIP = """<div style="display: flex; flex-direction: column; gap: 12px; align-items: flex-start;">
  <span class="what" style="font-weight: 600;">No heat<span class="tip-i" tabindex="0" aria-label="Why: Cold outside or tonight."><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg></span></span>
  <div class="tooltip on" role="tooltip" style="position: static;">Cold outside or tonight.</div>
</div>"""


def card(out, name, group, height, html, width=None):
    marker = (
        f'<!-- @dsCard group="{group}" height={height}'
        + (f" width={width}" if width else "")
        + " -->"
    )
    for src, (blob, _) in BLOB.items():
        html = html.replace(f"/owner/static/{src}", f"/_blob/{blob}")
    # Links, form actions and the search endpoint point at the app; a preview stays put.
    html = re.sub(r'="/owner/[^"]*"', '="#"', html)
    assert "/owner/" not in html.replace('href="#"', ""), name
    doc = f"""{marker}
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><style>body {{ margin: 0; padding: 16px; }}</style></head>
<body>
{html}
</body>
</html>
"""
    (out / "components" / name).mkdir(exist_ok=True)
    (out / "components" / name / "preview.html").write_text(doc)


def readme(out, sections, name, docs, classes, provides):
    body = "\n\n".join(sections[d] for d in docs)
    listed = "`, `".join(classes.split())
    text = (
        f"{body}\n\n**What you provide:** {provides}.\n\n"
        f"**Classes** (in `components/bundle.css`): `{listed}`.\n\n"
        "The preview is the owner view's own markup, captured from the running app; build it with "
        "the same classes.\n"
    )
    (out / "components" / name / "README.md").write_text(text)


def index(ref: str) -> dict:
    """project/design-system.json, the saved copy's index, naming the logo uploads."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    kinds = {".svg": "image/svg+xml", ".png": "image/png"}
    files = {
        name: {"name": name, "blob": blob, "size": size, "type": kinds[Path(name).suffix]}
        for name, (blob, size) in BLOB.items()
    }
    return {
        "v": 3,
        "layout": "files",
        "createdOnFiles": {"v": 1, "at": now},
        "title": TITLE,
        "namespace": "RevinOwnerView",
        "libraries": [],
        "sections": {},
        "groups": ["Logos"],
        "assetGroups": {
            "Logos": {"name": "Logos", "tile": "l", "order": list(files), "files": files}
        },
        "blobs": {},
        "docs": {"readme": "project/README.md", "sections": []},
        "lastChange": {
            "by": "Claude",
            "at": now,
            "via": f"scripts/export_design_system.py · {ref}",
            "note": "Rebuilt from app/static/owner and docs/design-system.md",
        },
    }


def export(out: Path, ref: str, base: str, username: str, password: str) -> None:
    out = out / "project"
    if out.exists():
        shutil.rmtree(out)
    for folder in ("fonts", "assets/Logos", "components/Cover"):
        (out / folder).mkdir(parents=True)
    tokens = build_tokens(ref)
    (out / "tokens.json").write_text(json.dumps(tokens, indent=2) + "\n")
    (out / "tokens.css").write_text(compiled_css(tokens))
    components_css = (REPO / "app/static/owner/components.css").read_text()
    (out / "components/bundle.css").write_text(BUNDLE_HEAD + components_css)
    shutil.copy(REPO / "app/static/owner/fonts/PublicSans-latin.woff2", out / "fonts")
    (out / "assets/Logos/README.md").write_text(LOGOS_README)
    rules = (REPO / "docs/design-system-rules.md").read_text()
    # The brand book starts after the doc's own title and opening paragraph.
    (out / "README.md").write_text(rules.split("\n\n", 2)[2])
    (out / "components/Cover/preview.html").write_text(COVER)
    sections = doc_sections()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(base + "/owner")
        page.fill("#username", username)
        page.fill("#password", password)
        page.click("button[type=submit]")
        page.wait_for_load_state()
        current = None
        for name, group, height, path, sel, docs, classes, provides in COMPONENTS:
            if path is None:
                card(out, name, group, height, TOOLTIP)
            else:
                if path != current:
                    page.goto(base + path)
                    page.wait_for_timeout(500)
                    current = path
                got = page.evaluate(PREP, sel)
                if not got:
                    raise SystemExit(f"{name}: nothing on {path} matches {sel}")
                width = got["width"] if got["width"] > 900 else None
                card(out, name, group, height, got["html"], width)
            readme(out, sections, name, docs, classes, provides)
        browser.close()
    (out / "design-system.json").write_text(json.dumps(index(ref), indent=2) + "\n")
    print(f"wrote {sum(1 for f in out.rglob('*') if f.is_file())} files to {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ref", required=True, help="the commit this export is built from")
    parser.add_argument("--base", default="http://127.0.0.1:8100")
    parser.add_argument("--username", default="owner")
    parser.add_argument("--password", default="dev-owner-password")
    args = parser.parse_args()
    export(args.out, args.ref, args.base, args.username, args.password)
