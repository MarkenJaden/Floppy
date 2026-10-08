# Fork Divergences & Intentional Customizations

This document serves as the canonical handoff and divergence specification for the `MarkenJaden/Floppy` fork.
It is consulted during automated upstream synchronization and CI validation (via AI resolvers and test evaluators) to distinguish intentional customizations from accidental regressions or merge defects.

---

## 1. Search Behavior & Defaults

- **Default Search Category**:
  - The default search category across the header search bar is **"Movies, TV & Anime"** (`movies_tv_anime`), matching the primary tracking workflow.
  - The standard "All" (`all`) search category is explicitly retained and searches across all supported media formats.
- **Search Placeholder & UI**:
  - In `src/templates/base.html`, the search form Alpine.js component dynamically adapts placeholder text according to media selection and viewport breakpoints (`window.matchMedia('(max-width: 639px)').matches`).
  - Search placeholder logic must preserve both mobile responsiveness and custom category naming ("Movies, TV & Anime" / "Everything").

---

## 2. Shared Lists & Collaboration

- **Collaborative Watched Toggles**:
  - 1-click watched check-off toggle on list items (`list_item_watched_toggle` in `src/lists/views.py` and list components) allowing instant status toggling.
  - Syncs real-time state across list collaborators.
  - Redundant duplicate card checkmarks are suppressed in favour of the clean list-level toggle.
- **MyAnimeList-Style Status Tabs**:
  - `src/templates/lists/components/list_status_tabs.html` provides tabs for filtering list entries by status (All, Watching / In Progress, Completed, On Hold, Dropped, Plan to Watch).
- **Completed Items Placement**:
  - `completed_placement` setting (`bottom` vs `normal`): completed items can be pushed to the bottom of the list regardless of the active sort order.
- **Collection Platform Resolution**:
  - List items resolve and display physical/digital platforms directly on the card where available.

---

## 3. Query Count Budgets (`src/app/tests/test_query_counts.py`)

- **`CUSTOM_LIST_DETAIL_MAX_QUERIES`**:
  - **Pin Value**: `55` (Upstream pin was `39`).
  - **Rationale**: Upstream added the Tiers board tab and tier sorting. The fork additionally renders list collaborator watch states, status tab counts, and collection platform lookups. This deliberate increase (+16 queries) is expected and pinned.

---

## 4. Theme & Styling Contracts

- **Theme Token Discipline**:
  - Floppy enforces strict theme token usage via `ThemeTokenContractTests`.
  - Hardcoded color utilities such as `text-indigo-400`, `text-gray-500`, or raw `dark:` variants are prohibited in template HTML.
  - All theme-aware styling must use CSS variables:
    - Links & active highlights: `text-[var(--color-link)]`
    - Muted secondary text: `text-[var(--color-text-muted)]`
    - Surfaces & borders: `bg-[var(--color-surface)]`, `border-[var(--color-border)]`

---

## 5. Deployment & Webhooks

- **Auto-Deployment**:
  - Changes pushed to `latest` trigger automated Coolify redeployments / container image updates.
  - The workflow must maintain clean, buildable commits that pass migrations and linting.
