# Architecture Docs — Conventions

The single source of truth for how the architecture docs are authored,
structured, labeled, and linked. `CLAUDE.md` points here. The docs are
**visual HTML, read rendered**; the emoji headings below are the map.

> **Concept vs. presentation — two layers, never mixed.** Everything here is the
> _design concept_: the screens, flows, contracts, and values a module is meant
> to have. The HTML/CSS that renders it — element `id`s, class names, the
> `wf-*`/`flow-*`/`stk-*` kits, file paths under `docs/` — is **presentation
> scaffolding**, not an implementation contract. It names visual constructs,
> never code symbols. When a design is implemented, the code derives its own
> names, IDs, schemas, endpoints, and enums from the design _intent_ — never from
> the markup. So never cite a doc's CSS class or `id` as something the code must
> mirror, and never read a markup detail as a product decision. The binding
> decisions are the prose, labels, and diagrams; the styling is disposable.

---

## 🍰 Layered model

```
L1  architecture-scheme.html        System hub — visual, clickable modules.
        ▼
L2  modules/<name>/index.html       Module page — overview + L3 navigation.
        ▼                 ▼                   ▼
L3  _workzone/*.html  _wireframes/*.html  _features/<f>/
    design topics     screen mockups      multi-screen flows
```

Detail increases as you descend; each layer links down to the next. Workzone,
wireframes, and features are three parallel L3 branches off the same module page
— all permanent, all deepening over time.

**L2 anatomy** — four parts, always in this order:

1. **Visual overview** — diagrams of layers, flows, key components.
2. **Workzone nav** — links to the `_workzone/` design pages.
3. **Wireframes nav** — links to the `_wireframes/` screens; this nav **is** the
   screen catalog (no separate index). Group labels carry the access rule
   (public vs authenticated). Every link carries a right-aligned kind chip
   (`.wz-tag`): the common kind is `.wz-tag--screen` («макет»); a rarer kind
   (e.g. `.wz-tag--home`, the start screen) **replaces** it so it stands out —
   one chip per link. A new kind = a new `.wz-tag--*` modifier with its own color.
4. **Features nav** — links to the `_features/` flows; this nav is the feature
   catalog. The lead link (the feature home) carries an accent left-stripe
   (`.wz-link--lead`); each subordinate link carries the same screen chip as the
   wireframes nav.

---

## 📁 Module lifecycle

A module graduates as it gets designed:

| Stage              | Layout                                                                                       |
| ------------------ | -------------------------------------------------------------------------------------------- |
| **Stub** (concept) | single file `modules/<name>.html`                                                            |
| **In design**      | directory `modules/<name>/`: `index.html` + `_workzone/` (+ `_wireframes/`) (+ `_features/`) |

- Names are **kebab-case**; the L3 directories are underscore-prefixed so they
  group together.
- A stub becomes a directory when detailed design begins. `_wireframes/` appears
  with the first screen, `_features/` with the first multi-screen flow.
- Each new page starts from its branch template: `_wireframes/_template.html`,
  `_features/_template/`.

---

## 🛠️ Workzone — design topics

The module's design space: **one page per topic** (`authentication`,
`authorization`, `routing`, `stack`, `tests`, …), deepening as the module
matures. It is the **module-altitude home**: mechanics and policies that span
screens — token lifecycle, password policy, the route map — are documented here,
and screens and features **link** rather than restate. Screens are not a
workzone topic; each screen is its own `_wireframes/` page.

A page whose content is a forecast (file structure, data model, schema) opens
with a `.draft-notice` — a bold page-specific lead followed by a short caveat
that this is a starting point for implementation, not a hard plan, updated after
the fact.

---

## 🖼️ Wireframes — screen mockups

The visual companion to the workzone: **what the user sees**, one file per screen
in `modules/<name>/_wireframes/`. Each page is two parts:

1. **Description** — minimal or absent; the mockup speaks for itself. When
   present, one short purpose phrase (`.desc` / `.note`) — what the screen is
   for, no mechanics, no state list. Don't repeat the route or access rule (they
   live in the chrome URL and the nav group label). Link `_workzone` once only,
   on a genuine dependency the screen doesn't show.
2. **Mockup** — composed from the `wf-*` kit in `modules/wireframe.css`,
   carrying **only real UI strings** — no captions, sources, backend logic in
   url-tips, a11y prose, or plaintext passwords.

**Hi-fi, not a pixel contract.** A wireframe is visually finished — close to the
shipping UI in hierarchy, spacing, and honest elements (chips, icons, status
states, filled fields with plausible data), not a schematic sketch. It's still
assembled from the kit with **no inline styles**, on the shared palette. The
markup is disposable (see top of file): when implemented, code derives its own
names and structure from the design intent, not the mockup.

**Compose from the kit.** `wireframe.css` ships the primitives — `.wf-stage`,
`.wf-screen`, `.wf-chrome`, `.wf-appbar`, `.wf-field`, `.wf-btn`, `.wf-alert`,
`.wf-annot`, layout helpers. Add CSS only when a screen needs a shape the kit
genuinely can't express — and put it in `wireframe.css`.

### Header badges

Two badges trail `.header__sub`, shell then load — each a cross-module link that
`make docs-map` wants reciprocal (same as the guard marker):

- **Shell** — the screen renders inside the module shell:
  `<a class="in-shell" href="layout.html"><span class="in-shell__frame"></span>shell</a>`.
  `layout.html` itself doesn't carry it; the template ships with it. Drop it only
  for a standalone full-page mockup that renders **outside** the shell.
- **Load** — the screen has a loading / waiting state; links to the matching
  mechanism in shared-ui's loading-states vocabulary:
  `<a class="in-load in-load--<mech>" href="…/shared-ui/_wireframes/loading-states.html#<anchor>"><span class="in-load__icon"></span>загрузка</a>`.
  Pick the mechanism shown on **cold start** (empty→content fill), not a
  secondary state — the glyph differs so the dominant wait reads at a glance:

  | Variant             | Anchor           | When                                                                                  |
  | ------------------- | ---------------- | ------------------------------------------------------------------------------------- |
  | `in-load--skeleton` | `#skeleton`      | data screen with a known content shape (detail view, cards, a simple list)            |
  | `in-load--keep`     | `#keep-previous` | a list using the `list-controls` search/filter/pagination pattern — refetch keeps rows |
  | `in-load--spinner`  | `#spinner`       | an action / form with nothing to fetch on open; the only wait is the submit           |
  | `in-load--progress` | `#progress`      | the shell's route transition — lives on `layout.html`, not content screens            |

  Templates ship the skeleton default; swap it when designing. Drop the marker
  only for a screen with no async at all (a static terminal page).

A destructive control also carries an inline **guard marker** linking to its
confirm dialog — the convention lives in § Shared UI.

### Annotation pins & legend

- **Pins.** A `.wf-annot` pin sits **trailing, right after the label it
  explains** — at the end of a row / hint, or inside a filled button
  (`.wf-annot--on-accent` on an accent surface). Never a leading pin. Numbering
  follows **reading order** (DOM top-to-bottom, left-to-right); the legend lists
  the notes in that same order.
- **Legend voice.** A note says what the element **is or does in the UI** — 1–2
  short, neutral sentences. No backend mechanics (column names, crypto, queues,
  locks); when they genuinely matter, link `_workzone` once instead.

### Stage background — screen vs modal

The diagonal hatch stands in for a modal scrim; a plain mat means a full screen.
The background alone tells the two apart, so it carries two shapes:

- **Modal over its screen** (the common case) — put the screen on a plain
  `.wf-stage`, then **nest** the dialog (`.wf-screen--modal`) in a `.wf-backdrop`
  inset _inside that same stage_, below the screen; the legend follows on the
  plain mat. Only the dialog sits on the hatch — it reads as the overlay covering
  the screen above, and the numbered notes belong to the screen, not the scrim.
  One stage = one screen's whole context (screen · its modals · legend).
- **Standalone modal mockup** — when the subject _is_ the dialog with no host
  screen behind it (e.g. `re-auth-modal`): put the whole stage on
  `.wf-stage--backdrop`; a legend on the hatch is fine, it describes the modal.

Never a full screen on a backdrop, nor a modal on a plain mat. Anchored overlays
(popovers, dropdown menus, tooltips) belong to their screen and stay on a plain
stage beside it — the hatch marks page-covering modals only.

**Overlays are interactive, never frozen open.** An anchored overlay — an action
menu, a filter facet, a tooltip — is hidden at rest and revealed on hover / focus
of its trigger, the way the chrome URL popover (`.wf-urltip`) already works; it
reserves no space. Build it with a `.wf-pop` host wrapping the **real trigger** (a
button, a filter pill, a chip) and the overlay (`.wf-menu` for an item list,
`.wf-tip` for a plain bubble). The captioned gallery frame names the variant
(`⋯ меню действий · Owner`), but the reader hovers the trigger to see the open
state — don't force an overlay permanently visible.

### Realistic URLs

The `.wf-chrome__url` is a design decision, not filler: write the **production
route** the screen will actually serve, host included
(`achilles.local/admin/users`), one host across a module. It's the screen's single
home for the route (the access rule lives in the nav group label); together the
wireframe URLs form the module's route map before React Router is wired — so
settle the path here.

**Linking.** A screen page is self-contained (its description is on the page), so
there's no intra-module back-link to maintain. A reference to another **module**
follows § Cross-references — bidirectional, checked by `make docs-map`.

---

## 🧩 Features — multi-screen flows

The layer wireframes lack: **how the user moves through screens** to reach a goal.
A wireframe answers "what does this screen look like"; a feature answers "what's
the journey" — triggers, transitions, branches, edge cases.

**A feature is a mini-module.** One sub-directory per feature in
`modules/<name>/_features/`, holding `index.html` (the feature home) plus the
feature's **exclusive** screens beside it — the same shape as a module's
`index.html` + its pages. A feature **earns a page only when multi-screen**; a
single screen is just a wireframe.

**Feature home = a map.** Up to three parts, and nothing else:

1. **Description** — what the feature is and the user journey, **product prose
   only**.
2. **Deps block** (optional) — feature-level dependencies with **no screen of
   their own** (SMTP, an external service) and their consequence (a fallback, the
   feature's `.iter` status). Never facts that already live on a screen.
3. **Flow diagram** — the screen storyboard with transitions.

🎯 **Altitude rule.** Each fact lives at one altitude and is **never copied up**
into the feature — copies drift and double the upkeep:

- **Screen altitude** (a screen's states, local rules, copy) → the screen's own
  legend / annotations.
- **Flow altitude** (a branch between screens) → a `.flow-branch` fork.
- **Module altitude** (token mechanics, password policy, route map) → `_workzone`.

The map only **links or forks** to these homes; the one thing it may state itself
is a screen-less dependency, in the deps block. Worked example:
`password-recovery`'s anti-enumeration and resend-rate-limit live on the
`forgot-password` legend, the expired-link case is a flow branch, token TTL is in
`_workzone`; the home states only its SMTP dependency (with the temp-password
fallback) and restates nothing else.

### Screen ownership — proximity principle

The codebase's rule for constants (live with the consumer, extract to shared at
2+ consumers), applied to screens. A **consumer** is a feature whose flow uses the
screen as **its own node** — not anyone who merely links to it.

- **Exclusive screen** (a node of this one feature's flow) →
  `_features/<feature>/<screen>.html`; same `wf-*` kit, CSS paths one level deeper.
- **Shared screen** (a standalone base screen like `login`, or a node reused by
  2+ features like `status-screen`) → `_wireframes/`; the feature **links**, never
  copies.
- **Promotion** — when a 2nd feature uses a local screen as its own node, **move**
  it to `_wireframes/` and relink; `make docs-map` flags the broken links — a
  managed refactor, not silent drift.
- **Feature-specific tweak of a shared screen** is a **state**, not a new screen:
  add the state to that screen's `_wireframes/` page and link the flow step to its
  anchor.

**Entry point ≠ reuse.** A base screen pointing _into_ a feature — `login`'s
"Forgot password?" → the recovery flow's first screen — is a navigation edge to
the feature's public start; it does **not** make the target shared. An
entry-point link points at the flow's **start screen**, the real UX target.

🎯 **Orphan test** decides the home: _delete this feature — does the screen still
have a home?_ No → it belongs in the feature. Yes → shared in `_wireframes/`.
`forgot-password` / `reset-password` orphan without password-recovery, so they
live in its dir; `login` and `status-screen` stand alone and stay shared. Screens
are **never duplicated** — pixels render in exactly one place.

**Reuse scope picks the home — screens _and_ sub-screen components.** The
proximity principle widens to any reusable unit (a field group, a
password-strength meter, a confirm modal). Three rungs, by how widely the unit
recurs:

- across **2+ modules / every entry point** → `shared-ui/_wireframes/`;
- within **one module**, used by 2+ of its screens → that module's `_wireframes/`;
- by **one feature** only → that `_features/<feature>/`.

A **consumer** here is any screen that renders the unit — a flow node _or_ a plain
base wireframe; the 2nd consumer triggers promotion up the ladder regardless of
kind. 🎯 No-duplication holds at component grain: a field group repeated across
five screens is **one** component composed by each, not five copies free to drift.
Centralize the _rules_ the same way — password constraints live once in
`authentication#password-policy`, every password screen xrefs it.

### Flow diagram

Build the storyboard from the `flow-*` kit in `feature-flow.css` — a **metro
line**: `.flow-stop` stations are screen nodes, joined by `.flow-seg` segments
labelled with the transition that fires them. The station name is an `<a>` to its
screen — plain `.flow-stop` for a feature-local one, `.flow-stop--shared` (doubled
ring) for a `_wireframes/` one.

An edge case that diverts the journey forks **down** from its segment via
`.flow-branch` (dashed = off the happy path); add `.flow--branch` to the `.flow`
to reserve the room. Only a branch reaching a _different screen_ earns a fork; one
that adds no screen (a limit, a retry) belongs on that screen's legend.

- **Feature → screen** links are intra-module (**Page** category, olive):
  `make docs-map` validates they resolve but requires no reciprocal — a
  storyboard pointing at screens is one-way by design.
- **Feature → another module** follows § Cross-references (**Module** category,
  accent) — bidirectional.

**Boundary with routing.** The `routing` workzone owns the system **route map**
(gate, guards, role redirects); a feature owns **one user-goal journey**
end-to-end. They cross-link, don't restate.

---

## 🧩 Shared UI — cross-cutting components

UI patterns that recur across every entry point (Admin Panel, Web App, …) live
once in `modules/shared-ui/` — a presentation **foundation**, not a system module.
It's a normal in-design directory (`index.html` + `_wireframes/`), but its screens
are reusable components rather than one product's pages:

- `_wireframes/toasts.html` — transient success / error feedback after an action;
- `_wireframes/confirm-dialog.html` — destructive-action confirmation, three
  variants under stable anchors: `#reversible`, `#irreversible`, `#type-to-confirm`;
- `_wireframes/loading-states.html` — the loading / waiting vocabulary the load
  badge points at: `#skeleton`, `#keep-previous`, `#spinner`, `#progress`;
- `_wireframes/system-screens.html` — platform state pages, each under its anchor:
  `#not-found` (404), `#server-error` (500), `#offline`, `#forbidden` (403),
  `#maintenance`.

Modules **link** to these, never copy them — the same proximity principle as
shared screens. The anchors are the stable link targets; renaming one breaks
inbound markers, so run `make docs-map` after touching them.

**Guard marker (`.wf-guard`).** A destructive control in any mockup carries a small
caution marker right after it, linking to the matching confirm-dialog variant — so
the dialog stays in one place and the affordance sits at the point of risk:

```html
<span class="wf-guarded"
  ><a class="wf-a wf-a--danger">Удалить</a
  ><a
    class="wf-guard"
    href="../../shared-ui/_wireframes/confirm-dialog.html#irreversible"
    title="Действие защищено подтверждением"
    ><span class="wf-guard__icon"></span></a
></span>
```

(styled in `wireframe.css`). The control and its guard always live inside a
`.wf-guarded` wrapper, never loose; when the control also carries an annotation
pin, the order is `[control][guard]` then the pin trailing after it. **Only
destructive actions** are marked — delete, revoke, deactivate, end-sessions;
routine actions stay unmarked, and system screens need no marker (they are reached
by a route, not a button). The marker → shared-ui link is cross-module, so
`make docs-map` wants the pair reciprocal: `shared-ui/index.html` links back to
each consuming module, which satisfies it at module altitude (the back-link may
lack the per-marker anchor — a `shallow` advisory, never a gate).

**Toasts aren't marked.** Every state-changing action is assumed to raise a
success-toast — marking each button would bury the screen. Link `toasts.html` only
from a legend where the behavior is non-obvious (e.g. an error toast on a "test
connection" button).

**On the L1 hub** Shared UI is a secondary `.foundation` pointer beneath the Entry
Points layer — not a system block, not a cross-cutting band like Auth / Security.
Auth genuinely cuts across all layers (backend included); the UI foundation
touches the front ends only, so it reads as a quiet link under the layer it
serves.

---

## 🏷️ Iteration labels — `done` / `v1` / `v2`

Mark _when_ a piece of the architecture is built. They apply to **every artifact**
— overview items, workzone topics, wireframe screens, feature flows and their
screens. Styled by `.iter` in `docs/shared.css`.

| Label  | Color  | Meaning                  |
| ------ | ------ | ------------------------ |
| `done` | orange | implemented              |
| `v1`   | green  | current iteration scope  |
| `v2`   | gray   | backlog / next iteration |

**Promotion cycle:** all `v1` is `done` → mark it `done` → pull the next items
from `v2` into `v1`.

**One label per page — the page is the unit of scope.** A designed page (a
workzone topic, a wireframe screen, a feature flow or its screen) carries
**exactly one** `.iter` pill, in its `<h1 class="header__title">`. That page-level
label sets the version of the whole page:

- **page-level `v1`** — the whole page ships in v1, **except** items explicitly
  marked `v2`. A `v1` page reads as "all of this, minus the `v2` exceptions".
  Never sprinkle `v1` pills on individual items inside a `v1` page — the only
  pills in the body are the `v2` exceptions.
- **page-level `v2`** — the whole page is deferred; no per-item pills at all (the
  title says it once).

`done` is orthogonal: it marks a row whose backing already exists (e.g. a
test-stack row), and stays wherever it is regardless of the page label.

A version marker is **always** the `.iter` pill — including mid-sentence in prose
for a `v2` exception. Never write a bare `v1` / `v2` as plain or bold text; the
filled background is what makes it read as a marker at a glance. The L3 templates
ship a page-level `v1`, so a new page inherits the default and you only add `v2`
exceptions.

**L2 overview is the one exception** — a module `index.html` is a _catalog_ of
many items at mixed versions, not a single artifact, so it carries no page-level
label. Its items take compact modifier classes for subtle hints (`mod-tag--v2`,
`prot-item--v2`); the class alone carries the marker (dashed border + muted
color), so **never repeat "v2" in the text**:
`<span class="mod-tag mod-tag--v2">SSO/OIDC</span>`.

---

## ✦ AI marker — `.ai-tag`

Flags a **stage / task that runs model inference** — embedding, processing, RAG,
agent reasoning, a user's chat query — so the AI touchpoints read at a glance, set
apart from the config / plumbing around them (provider setup, discovery, cost
tracking carry no inference). A static inline label (sparkle glyph + `AI`), styled
by `.ai-tag` in `docs/shared.css`:

```html
<span class="ai-tag" title="Этап использует нейросеть — инференс модели"
  ><span class="ai-tag__icon"></span>AI</span
>
```

- **Mark the step, not the surroundings** — attach it where a model-consuming task
  is **named** (`Query Engine (RAG)`), never to a whole section that merely
  discusses models.
- **First / defining mention** — mark a task at the catalog where it's enumerated
  (e.g. the AI Platform pool list), not at every later re-reference; same dedup
  discipline as cross-links.
- Cross-cutting and reusable — the same tag flags inference steps in any module
  (Harvester pipeline, Query Engine, Agent Engine).

---

## 🔗 Cross-references

Links between sections — **three categories by where the link reaches**, each its
own color so the destination reads at a glance. Two sizes: an inline word in
running text, or a pill embedded in a heading.

| Category   | Reaches                     | Hue    | Inline         | Badge               | Reciprocal?             |
| ---------- | --------------------------- | ------ | -------------- | ------------------- | ----------------------- |
| **Module** | another module              | accent | `.xref-module` | `.xref-badge`       | **yes** — bidirectional |
| **Page**   | another page in this module | olive  | `.xref-page`   | `.xref-badge--page` | no                      |
| **Anchor** | a block on this page        | dark   | `.xref-anchor` | — (inline only)     | no                      |

The hues line up with what `make docs-map` sees: it counts an edge as cross-module
only when the target file lives in **another module** — exactly the accent links.
Every accent link owes a reciprocal; olive and dark never do.

**Anchor** — a stable kebab-case `id` on the section a link lands on:
`<div class="section" id="tokens-sessions">`. The same anchors serve all three
categories.

**Inline vs badge.** Inline reads as a word mid-sentence, at the surrounding font
size, **no** leading arrow: a **semibold underlined word** colored by destination
(accent module · olive page · dark anchor), no fill — the hue and underline
together are the affordance. The badge (`.xref-badge`, `.xref-badge--page`) is the
pill form for a section **title** — a filled, mono, uppercased pill with a leading
`→`, sharing the category hue. Badge when a whole section mirrors the target;
inline when the target is named inside prose.

- **Label = the target's name, nothing else** — a module link names the module
  (`→ Admin Panel`), a page link the page/section, an anchor link the block
  (`↑ Refresh token rotation`). The _topic_ comes from the anchor and the host
  section — never bake it into the label (it drifts on rename, and the validator
  can't see it).
- **Pending** (`.xref-badge--pending`) — a `<span>` with no `href`, muted +
  dashed, for a cross-module target that isn't linkable yet (stub / `v2`). Promote
  to `<a class="xref-badge">` once the page and anchor exist. A pending `<span>`
  doesn't count as a direction — the reciprocal is owed only once it becomes an
  `<a>`.
- **One link = one topic** — a link lands on the anchor whose section actually
  documents that topic, not merely the same file or module. When a sentence
  enumerates several topics, give **each** its own link.

**Link density — when to link at all.** A link is an invitation to navigate, not a
highlight on a term. These rules keep links scarce enough to mean something:

- **Link = the target holds what this text omits** — the mirror of "cross-link,
  don't restate": link when the reader may genuinely need the details left out
  here. A passing mention with no informational dependency stays plain text.
- **First mention per section** — within one section (a block with an `id`) a
  target is linked once, at its first mention; repeats stay plain. Another section
  may link it again — readers arrive by anchor, not linearly, so the dedup scope
  is the section, not the page.
- **A badge covers its section** — when a section title carries a badge to X, the
  body holds no inline links to X.
- **Section-wide need → badge, not inline** — when the whole section leans on a
  target, the link moves up into the title badge and the prose stays clean. Inline
  is for a dependency of one specific sentence.
- **Two links in a row = overload** — adjacent colored links in one sentence
  signal either mentions that don't need linking or an enumeration better unfolded
  into a list, one link per line.
- **Mockups are exempt** — repeated identical controls inside a wireframe mockup
  (`wf-a`) depict the real UI and are never deduplicated; the surrounding legend
  and annotations follow the rules above.
- **Anchor links are the strictest** — the target block is one scroll away, so a
  dark anchor link must mark a real dependency, not every mention of a term that
  happens to have an `id`.

Dedup never threatens reciprocity: `make docs-map` needs **one** link per
direction between modules — extra copies add noise, not validation.

⚠️ **The validator is structural, not semantic.** `make docs-map` confirms anchors
exist, modules link both ways, and each xref **hue matches its destination
category** (a mismatch is flagged _miscolored_ — warning by default, error under
`--strict`). It still can't see whether a link points at the _right_ anchor, or
whether one link aggregates a topic that should be split (both pass clean — catch
them by reading). Renaming an `id` silently breaks inbound links — run
`make docs-map` after touching any anchor or link.

---

## 🖱️ Hover = "clickable"

Hover feedback promises the element can be clicked, so only clickable things
(`<a>`, buttons) may react on hover (color, border, lift) and show a pointer
cursor. Static elements (labels, cards, badges, tags) get **no** hover reaction and
keep the default cursor.

**Bind the effect to the link, not the visual class** — so a non-clickable
instance stays inert. Scope to the anchor (`a.xref-badge:hover`) or, when a wrapper
makes a block clickable, to the wrapper (`.block-link:hover .block`, never
`.block:hover`).

**Carve-out:** non-affordance motion is fine — the `:target` landing highlight and
the hub's data-flow pulse don't imply "click me".

---

## 🧭 Back-link scroll restoration — `doc-nav.js`

The single JS file in the docs (`docs/architecture/doc-nav.js`): it makes the
in-page `← back` link restore the scroll position on the page you left, like the
browser's Back button. Every page saves its position on leave (sessionStorage), a
click on `a.back` flags the target, and the target restores on load; without
storage the link degrades to a plain navigation.

Loaded with `defer` by the hub and **every** module page —
`<script src="…/doc-nav.js" defer></script>` as the last line of `<head>`, with the
same depth-relative prefix logic as the CSS links (§ Styling). The L3 templates
already include it. Keep the docs otherwise JS-free: behavior beyond this
navigation aid needs a new convention here first.

---

## ✅ Validation & map — `make docs-map`

A generated map + validator over the HTML
(`docs/architecture/_tools/docs_map.py`, stdlib-only): it collects all `id` anchors
and `<a href>` links, prints the **module-level connection graph**, and checks in
tiers:

- broken anchors / missing files → **error**, exit 1;
- one-way cross-module links, duplicate ids → **warning** (error under `--strict`);
- **unflashable anchors** — a deep-link whose target gets no `:target` landing
  flash, so the reader lands without the highlight that orients them → **warning**
  (error under `--strict`);
- **miscolored xref links** — an `xref-*` class whose hue claims a destination
  category the link doesn't reach (`xref-module` to a same-module page, `xref-page`
  to another module or a same-page anchor, `xref-anchor` to another file, and the
  badge equivalents). The link resolves but renders the wrong color → **warning**
  (error under `--strict`);
- shallow back-links (one side links to a section, the other only to the module's
  front door) → **advisory** (`shallow`).

The flash allow-list is **derived from the CSS itself** — the validator scans every
stylesheet for `…:target` rules, so adding a `:target` rule for a new block type
automatically teaches it that the block is a valid landing target. No hand-kept
list to drift. So a link must land on a block that has a `:target` rule (§ Styling);
if it doesn't, point the link at the enclosing block that does, or give the new
block its own `:target` rule.

**Output is JSON by default** — the primary consumer is tooling (the
`vs-audit-module` skill parses it). `make docs-map` (= `--human`) prints the text
summary. Flags combine in either format: `--full` adds a per-file anchor listing,
`--strict` gates on warnings; the exit code is format-independent.

---

## 🎨 Styling — centralized CSS

No inline styles. Stylesheets split by scope, mirroring the repo's proximity
principle: shared chrome and reusable kits stay central; a module's bespoke
diagrams live with the module.

| File                                                           | Scope                                                                                                                                                                    |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `docs/shared.css`                                              | design tokens (colors, geometry, fonts), reset, `.iter` labels — used by everything                                                                                      |
| `architecture-scheme.css`                                      | the hub page only                                                                                                                                                        |
| `modules/module.css`                                           | shared page chrome for every module page: header · section · notes · checklist · steps · `wz-nav` · `xref-*` · file-tree · decision-box · `mod-*` primitives             |
| `modules/wireframe.css`                                        | the `wf-*` screen-mockup kit — loaded by every screen page (`_wireframes/*` and `_features/<f>/` screens)                                                                |
| `modules/feature-flow.css`                                     | the `flow-*` metro-storyboard kit — loaded by `_features/<f>/index.html`                                                                                                 |
| `modules/layered-stack.css`                                    | the `stk-*` layered-stack kit (request descends through layer bands + side channel) — loaded by workzone pages that use it                                               |
| `modules/tests.css`                                            | the `test-*` kit for `tests.html` pages (table + card grid)                                                                                                              |
| `modules/swimlane.css`                                         | the `swl-*` swimlane kit for `stack.html` pages (lanes × stack/structure columns, library pills) — page-specific callouts stay as deltas in `_workzone/stack.css`        |
| `<module>/<name>.css` (promoted) · `modules/<stub>.css` (stub) | a single module's bespoke diagrams, loaded only by its own pages — e.g. `auth-security/auth.css`, `admin-panel/admin.css`, `harvester/harvester.css`, `agent-engine.css` |
| `<module>/_workzone/<page>.css`                                | one workzone page's bespoke diagrams, next to the page that loads them — e.g. `auth-security/_workzone/routing.css`                                                      |

All kit and module sheets load on top of `module.css`.

**Placement** — a class's home, as a promotion ladder (a diagram is born in its
page's sheet and rises as consumers multiply):

1. tokens / cross-cutting → `shared.css`
2. a reusable **kit** (a visual language meant for many modules) → its own kit sheet
3. chrome shared by 2+ module pages → `module.css`
4. a diagram shared by 2+ pages of **one module** → that module's own sheet
   (`<module>/<name>.css`), co-located
5. a **single page's** bespoke diagram → a per-page sheet next to the page
   (`_workzone/<page>.css`)
6. hub-only → `architecture-scheme.css`

A second consumer page promotes a diagram to the module sheet; a second consumer
module promotes it to `module.css` or a kit. A stub's single sheet splits into
per-page sheets when its workzone pages appear — the module sheet keeps only
`index.html` diagrams and genuinely module-shared parts.

**Shared element defaults** — `module.css` ships page-wide defaults so page sheets
carry only deltas:

- inline `<code>` renders as the standard code chip (mono, accent on a faint tint);
  a context with its own code voice overrides just its deltas, and wireframe
  mockups cancel it wholesale (`.wf-stage code` in `wireframe.css`);
- `.mod-label` — the uppercase mono heading for a column / panel inside a diagram;
  compose in HTML (`class="mod-label swl-col__head"`) and keep only size / tracking
  / margin deltas in the page sheet;
- layer accent presets (`--stk-lc` / `--stk-lc-pale` per `.stk-layer--*`) are
  deliberately **not** shared: the kit owns the mechanism, each page names and
  colors its own layers.

**Comment style** — three levels, top-down:

1. **File header** (every sheet): scope (`<Scope> — what`), optional visual-language
   prose, `Loaded by … on top of …`, and the placement pointer (`Placement
   convention: … §Styling`).
2. **`═══` banner** (43 chars): names a diagram group — only in sheets with two or
   more groups; a single-diagram sheet describes itself in the header.
3. **`/* ── Name — optional gloss ── */`** one-liner: a subsection inside a group.

Free-standing notes are sentence case; trailing inline comments stay lowercase
fragments. Section names carry no module prefix (the header already scopes the
file). English only, and never a literal `*/` inside comment text (write a glob
like `*/tests.html` as prose — it terminates the comment and swallows the next rule).

**CSS link paths by page location** — each page loads `shared.css` + `module.css`,
plus the kit / module sheets noted above:

| Page                                           | `shared.css`                | `module.css`                          | + kits / diagrams                                                                    |
| ---------------------------------------------- | --------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------ |
| `architecture-scheme.html`                     | `../shared.css`             | — (`architecture-scheme.css` instead) |                                                                                      |
| `modules/<name>.html` (stub)                   | `../../shared.css`          | `module.css`                          | `<name>.css`                                                                         |
| `modules/<name>/index.html`                    | `../../../shared.css`       | `../module.css`                       | `<name>.css`                                                                         |
| `modules/<name>/_workzone/*.html`              | `../../../../shared.css`    | `../../module.css`                    | `../../layered-stack.css` · `../../tests.css` · `../../swimlane.css` · `../<name>.css` · `<page>.css` |
| `modules/<name>/_wireframes/*.html`            | `../../../../shared.css`    | `../../module.css`                    | `../../wireframe.css`                                                                |
| `modules/<name>/_features/<f>/index.html`      | `../../../../../shared.css` | `../../../module.css`                 | `../../../feature-flow.css`                                                          |
| `modules/<name>/_features/<f>/*.html` (screen) | `../../../../../shared.css` | `../../../module.css`                 | `../../../wireframe.css`                                                             |

---

## 🤖 Authoring & token efficiency

- **HTML is the single source of truth for the architecture** — navigate it with
  `grep` (`id=` anchors, `xref` links) and `make docs-map`. A parallel summary or
  index would be a second source that drifts.
- CSS is fully externalized, so each HTML file is a thin semantic skeleton plus
  content — cheap to read and diff. Keep it that way.
- Authoring stays in HTML: the visual diagrams live only there, and prose costs the
  same tokens either way.
