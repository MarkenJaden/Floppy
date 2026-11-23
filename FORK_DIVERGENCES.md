# Fork Divergences — MarkenJaden/Floppy (`latest` vs `dannyvfilms/Floppy` `latest`)

> Maintained for `MarkenJaden/workflows` automated upstream sync (`@main`).
> Custom fork features in this repository MUST be preserved across upstream merges and are NOT pushed upstream to `dannyvfilms/Floppy`.

---

## Preserved Fork Features

1. **Upstream Sync Master Workflow & CI/CD**
   - `.github/workflows/upstream-sync.yml` references `MarkenJaden/workflows/.github/workflows/upstream-sync.yml@main` with `workflow_sync_policy: preserve_if_no_pat` and `post_merge_workflow: docker-image.yml`.
   - `.github/scripts/gemini_test_evaluator.py` assists with known fork test divergence reconciliation when tests fail after an upstream merge.
   - `FORK_DIVERGENCES.md` documents fork-owned paths, customizations, and merge conflict invariants.
   - `.github/workflows/docker-image.yml` and `docker-compose.yml` include GHCR publishing and Coolify deployment webhook triggers on push to `latest` / `release`.

2. **Cross-Category Search & Progressive Streaming**
   - **Default Search Category**:
     - The default search category across the header search bar is **"Movies, TV & Anime"** (`movies_tv_anime`), matching the primary tracking workflow.
     - The standard **"All"** (`all`) search category is explicitly retained and searches across all supported media formats.
   - **Progressive Priority Streaming (`media_search_group`)**:
     - `src/app/search_views.py` (`SEARCH_ALL_PRIORITY_ORDER`, `media_search_group`), `src/app/urls.py`, `src/app/views.py`, `src/app/save_views.py`, `src/app/templatetags/app_tags.py`.
     - Streams search results progressively by priority (movies/shows first, then books, music, podcasts).
   - **Search Placeholder & UI**:
     - `src/templates/base.html`: Alpine.js search form dynamically adapts placeholder text by media selection and viewport breakpoints (`window.matchMedia('(max-width: 639px)').matches`), preserving active page `media_type` while keeping `movies_tv_anime`/`all` support.
     - `src/templates/app/search.html`, `src/templates/app/components/search_group_results.html`, `src/app/tests/test_search_suggestions.py`, `src/app/tests/views/test_media_search.py`.
   - **User Search Preference Migration**:
     - `src/users/models.py`, `src/users/migrations/0150_remove_user_last_search_type_valid_and_more.py` (fork-owned migration extending `last_search_type` choices/constraint with `movies_tv_anime` and `all`).

3. **Shared Lists Collaboration, MAL Status Tabs, Completed Placement & Collection Bulk-Add**
   - **Collaborative Watched Toggles & Real-Time Sync**:
     - `src/lists/collaborator_sync.py`, `src/lists/tests/test_collaborator_sync.py`.
     - 1-click watched check-off toggle (`list_item_watched_toggle` in `src/lists/views.py` / `src/lists/views_list_actions.py`) syncing state across list collaborators without duplicate card checkmarks (`src/app/card_surfaces.py`, `src/templates/app/components/media_card.html`).
   - **MyAnimeList-Style Status Tabs & Display Settings**:
     - `src/lists/models.py`, `src/lists/migrations/0017_customlist_completed_placement_and_more.py` (`completed_placement`, `default_layout`, `default_sort`, `default_sort_direction`, `status_tab`).
     - `src/lists/views_helpers.py`, `src/lists/views.py`, `src/lists/urls.py`, `src/lists/tests/test_list_collaboration_and_mal_tabs.py`.
     - `src/templates/lists/components/list_status_tabs.html`, `list_completion_stats.html`, `list_item_watched_toggle.html`, `_list_item_watched_button.html`, `list_toggle_refresh_handler.html`, `list_table.html`, `media_grid.html`, `src/templates/lists/list_detail.html`, `src/templates/app/components/table_items.html`, `src/templates/app/components/cells/media_status_cell.html`.
   - **Cross-Category List Add & Collection Bulk-Add**:
     - `src/lists/views_add_reorder.py`, `src/lists/views_list_actions.py`, `src/lists/tests/test_views.py`, `src/app/providers/tmdb.py`.
     - `src/templates/lists/add_item.html`, `src/templates/lists/components/add_item_search_all.html`, `src/templates/lists/components/add_item_search_group.html`, `src/templates/lists/components/collection_add_modal.html`, `src/templates/app/components/detail_secondary_content.html`.
   - **German Locale Updates**:
     - `src/locale/de/LC_MESSAGES/django.po` & `django.mo`.

4. **Query Count Budgets (`src/app/tests/test_query_counts.py`)**
   - **`CUSTOM_LIST_DETAIL_MAX_QUERIES`**:
     - **Pin Value**: `61` (Upstream pin is `44`).
     - **Rationale**: Upstream added Tiers board, viewers prefetch, and alphabet lookup; the fork additionally renders list collaborator watch states, MAL status tab counts, and collection platform lookups.

5. **Theme & Styling Contracts (`ThemeTokenContractTests`)**
   - Hardcoded color utilities (`text-indigo-400`, `text-gray-500`, `dark:`, etc.) are prohibited in template HTML.
   - Use CSS theme tokens: `text-[var(--color-link)]`, `text-[var(--color-text-muted)]`, `bg-[var(--color-surface)]`, `border-[var(--color-border)]`.

---

## Merge Conflict Policy

- **Fork-owned files (preserve across upstream merges)**:
  - `.github/workflows/upstream-sync.yml`, `.github/scripts/gemini_test_evaluator.py`, `FORK_DIVERGENCES.md`
  - `src/lists/collaborator_sync.py`, `src/lists/tests/test_collaborator_sync.py`, `src/lists/tests/test_list_collaboration_and_mal_tabs.py`
  - `src/lists/migrations/0017_customlist_completed_placement_and_more.py`, `src/lists/migrations/0019_merge_20261123_0936.py`, `src/users/migrations/0150_remove_user_last_search_type_valid_and_more.py`, `src/users/migrations/0159_merge_20261123_0937.py` (preserve released fork migrations and add a Django merge migration if upstream adds competing leaf migrations)
  - `src/templates/app/components/search_group_results.html`, `src/templates/lists/components/_list_item_watched_button.html`, `src/templates/lists/components/list_item_watched_toggle.html`, `src/templates/lists/components/list_status_tabs.html`, `src/templates/lists/components/list_completion_stats.html`, `src/templates/lists/components/add_item_search_all.html`, `src/templates/lists/components/add_item_search_group.html`, `src/templates/lists/components/collection_add_modal.html`
- **Shared files (accept upstream improvements while keeping fork hooks intact)**:
  - `.github/workflows/docker-image.yml`, `docker-compose.yml` (keep Coolify webhook trigger and fork image settings)
  - `src/app/search_views.py`, `src/app/urls.py`, `src/app/views.py`, `src/app/save_views.py`, `src/app/templatetags/app_tags.py`, `src/app/card_surfaces.py`
  - `src/lists/models.py`, `src/lists/urls.py`, `src/lists/views.py`, `src/lists/views_add_reorder.py`, `src/lists/views_helpers.py`, `src/lists/views_list_actions.py`
  - `src/users/models.py`, `src/app/tests/test_query_counts.py`
  - `src/templates/base.html`, `src/templates/app/search.html`, `src/templates/app/components/media_card.html`, `src/templates/lists/list_detail.html`, `src/templates/lists/add_item.html`
