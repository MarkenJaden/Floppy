"""Transactional, explicit reconciliation of personal episode watch history."""

# Messages are user-facing validation details and deliberately remain beside
# the branch that detects the invalid resolution.
# ruff: noqa: EM101, EM102, TRY003

import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from app.models import TV, Episode, Item, Season
from app.models.episode_order import EpisodeOrder, EpisodeOrderChange

WATCH_FIELDS = ("start_date", "end_date", "score", "notes", "dropped", "status")


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, cls=DjangoJSONEncoder,
    ).encode()).hexdigest()


def persist_order(show, provider, series_id, key, label, catalogue):
    """Persist a new immutable revision instead of relabelling existing items."""
    order, _ = EpisodeOrder.objects.get_or_create(
        show=show, provider=provider, series_id=str(series_id), key=key,
        revision=_digest(catalogue), defaults={"label": label, "catalogue": catalogue},
    )
    return order


def load_order(show, provider, series_id, key, *, user=None):
    """Fetch and persist a complete provider catalogue."""
    from app.providers.episode_orders import fetch_order

    catalogue = fetch_order(provider, series_id, key, user=user)
    return persist_order(show, provider, series_id, key, catalogue["label"], catalogue)


def metadata_for_order(media_type, order, season_numbers=None, episode_number=None):
    """Adapt the pinned catalogue to the application's existing metadata shapes."""
    rows = order.catalogue["episodes"]
    base = {
        "media_id": order.media_id, "source": order.provider,
        "media_type": "tv", "title": order.show.title,
        "image": order.show.image, "synopsis": order.show.synopsis,
        "details": {}, "related": {"seasons": []},
    }
    for number in sorted({row["season_number"] for row in rows}):
        episodes = [{
            **row, "id": row["provider_episode_id"], "name": row["title"],
            "still_path": row.get("image"), "vote_average": 0, "vote_count": 0,
        } for row in rows if row["season_number"] == number]
        season = {
            **base, "media_type": "season", "season_number": number,
            "season_title": f"Season {number}", "episodes": episodes,
            "max_progress": len(episodes), "score": None, "score_count": 0,
            "details": {"episodes": len(episodes)},
        }
        base["related"]["seasons"].append({
            "media_id": order.media_id, "source": order.provider,
            "media_type": "season", "title": order.show.title,
            "season_number": number, "image": order.show.image,
        })
        base[f"season/{number}"] = season
    base["details"] = {"seasons": len(base["related"]["seasons"]), "episodes": len(rows)}
    if media_type == "episode":
        number = season_numbers[0] if isinstance(season_numbers, (list, tuple)) else season_numbers
        row = next((row for row in rows if row["season_number"] == number
                    and row["episode_number"] == episode_number), None)
        if row is None:
            raise ValueError("Episode is absent from the selected order")
        return {**base, **row, "media_type": "episode", "name": row["title"],
                "id": row["provider_episode_id"], "runtime_minutes": row.get("runtime")}
    if media_type == "season":
        number = season_numbers[0] if isinstance(season_numbers, (list, tuple)) else season_numbers
        return base[f"season/{number}"]
    return base


def _watches(tv, *, lock=False):
    rows = Episode.objects.filter(related_season__related_tv=tv,
                                  related_season__order_archived=False).order_by("pk")
    return rows.select_for_update() if lock else rows


def _state(tv, order, rows):
    return {
        "tv_id": tv.pk, "active_order": tv.active_episode_order_id,
        "order_id": order.pk, "catalogue": order.catalogue,
        "watches": [{"id": row.pk, "item_id": row.item_id,
                     "related_season_id": row.related_season_id,
                     "created_at": row.created_at, "watch_operation_id": row.watch_operation_id,
                     **{field: getattr(row, field) for field in WATCH_FIELDS}} for row in rows],
    }


def preview_change(tv, order):
    """Propose only stable-ID matches, never equal episode coordinates."""
    if order.show_id != tv.item_id:
        raise ValueError("Episode order belongs to another show")
    rows = list(_watches(tv).select_related("item"))
    identities = {row["provider_episode_id"] for row in order.catalogue["episodes"]}
    mappings = []
    for row in rows:
        proven = row.item.source == order.provider and row.item.provider_episode_id in identities
        mappings.append({"watch_ids": [row.pk], "episode_ids": [row.item.provider_episode_id]
                         if proven else [], "archive": False})
    return {"token": _digest(_state(tv, order, rows)), "resolutions": mappings,
            "watches": _state(tv, order, rows)["watches"]}


@transaction.atomic
def apply_change(tv, order, *, token, resolutions):
    """Apply a fully resolved preview without emitting external watch decisions."""
    from app.services.watch_state import project_watch_state

    tv = TV.objects.select_for_update().get(pk=tv.pk, user_id=tv.user_id)
    order = EpisodeOrder.objects.select_for_update().get(pk=order.pk)
    if order.show_id != tv.item_id:
        raise ValueError("Episode order belongs to another show")
    rows = list(_watches(tv, lock=True))
    before = _state(tv, order, rows)
    if _digest(before) != token:
        raise ValueError("History or catalogue changed; preview again")
    by_id = {row.pk: row for row in rows}
    catalogue = {row["provider_episode_id"]: row for row in order.catalogue["episodes"]}
    seen = set()
    for resolution in resolutions:
        sources = resolution.get("watch_ids", [])
        targets = resolution.get("episode_ids", [])
        if not sources or len(set(sources)) != len(sources) or seen.intersection(sources) or not set(sources) <= by_id.keys():
            raise ValueError("Every watch must be resolved exactly once")
        seen.update(sources)
        if resolution.get("archive"):
            if targets:
                raise ValueError("Archived watches cannot also map to episodes")
            continue
        if not targets or len(set(targets)) != len(targets) or not set(targets) <= catalogue.keys():
            raise ValueError("Select destination episode identities or explicitly archive")
        if len(sources) > 1 and len(targets) > 1:
            raise ValueError("Resolve combinations separately from splits")
        fields = resolution.get("fields", {})
        if not set(fields) <= set(WATCH_FIELDS):
            raise ValueError("Unsupported watch metadata")
        for field in WATCH_FIELDS:
            if len({str(getattr(by_id[source], field)) for source in sources}) > 1 and field not in fields:
                raise ValueError(f"Explicitly resolve conflicting {field}")
    if seen != by_id.keys():
        raise ValueError("Every watch must be resolved exactly once")
    Episode.all_objects.filter(pk__in=by_id).update(order_archived=True)
    Season.all_objects.filter(related_tv=tv).update(order_archived=True)
    touched = {row.item_id for row in rows}
    for resolution in resolutions:
        if resolution.get("archive"):
            continue
        source = by_id[resolution["watch_ids"][0]]
        values = {field: getattr(source, field) for field in WATCH_FIELDS}
        values.update(resolution.get("fields", {}))
        for index, identity in enumerate(resolution["episode_ids"]):
            metadata = catalogue[identity]
            season_item, _ = Item.objects.get_or_create(
                media_id=order.media_id, source=order.provider, media_type="season",
                season_number=metadata["season_number"], episode_number=None,
                defaults={"episode_order": order, "title": tv.item.title, "image": tv.item.image},
            )
            season = Season.all_objects.filter(related_tv=tv, item=season_item).first()
            if season is None:
                season = Season(related_tv=tv, item=season_item, user=tv.user, status=tv.status)
                Season.all_objects.bulk_create([season])
            Season.all_objects.filter(pk=season.pk).update(order_archived=False)
            item, _ = Item.objects.get_or_create(
                media_id=order.media_id, source=order.provider, media_type="episode",
                season_number=metadata["season_number"], episode_number=metadata["episode_number"],
                defaults={"episode_order": order, "provider_episode_id": identity,
                          "title": metadata["title"], "image": metadata.get("image") or "",
                          "runtime_minutes": metadata.get("runtime")},
            )
            touched.add(item.pk)
            if index == 0:
                Episode.all_objects.filter(pk=source.pk).update(
                    item=item, related_season=season, order_archived=False, **values,
                )
            else:
                clone = Episode(item=item, related_season=season, **values)
                clone.full_clean(exclude=["watch_operation_id"])
                Episode.all_objects.bulk_create([clone])
                Episode.objects.filter(pk=clone.pk).update(created_at=source.created_at)
    TV.objects.filter(pk=tv.pk).update(active_episode_order=order)
    journal = EpisodeOrderChange.objects.create(
        user=tv.user, tv=tv, order=order, mappings=resolutions,
        before_state=json.loads(json.dumps(before, cls=DjangoJSONEncoder)),
    )
    for item in Item.objects.filter(pk__in=touched):
        project_watch_state(tv.user, item, record_changes=False)
    return journal
