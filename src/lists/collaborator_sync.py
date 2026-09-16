import contextlib
import logging

from django.db.models import Q
from django.utils import timezone

from app import cache_utils, fork_services_episode
from app.models import TV, Episode, MediaTypes, Season, Status
from lists.models import CustomList

logger = logging.getLogger(__name__)


def get_collaborators_for_item(item, source_user):
    """Find other users who share a list with source_user containing item."""
    if (
        not item
        or not source_user
        or not getattr(source_user, "is_authenticated", False)
    ):
        return set()

    q_items = Q(items=item)
    if item.media_type in (
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
        MediaTypes.EPISODE.value,
    ):
        q_items |= Q(
            items__media_id=item.media_id,
            items__source=item.source,
            items__media_type__in=(
                MediaTypes.TV.value,
                MediaTypes.SEASON.value,
                MediaTypes.EPISODE.value,
            ),
        )

    shared_lists = (
        CustomList.objects.filter(q_items)
        .filter(
            Q(owner=source_user, collaborators__isnull=False)
            | Q(collaborators=source_user)
        )
        .distinct()
        .prefetch_related("collaborators", "owner")
    )

    collaborators = set()
    for custom_list in shared_lists:
        members = set(custom_list.collaborators.all())
        members.add(custom_list.owner)
        members.discard(source_user)
        collaborators.update(members)

    return collaborators


def sync_media_to_list_collaborators(media, source_user):
    """Automatically sync completion to collaborators on shared lists containing this item."""
    if not media or not hasattr(media, "item") or not media.item:
        return

    # Sync when marked completed (Option 2: shared list completion sync)
    if getattr(media, "status", None) != Status.COMPLETED.value:
        return

    collaborators = get_collaborators_for_item(media.item, source_user)
    if not collaborators:
        return

    model_class = media.__class__

    for collab_user in collaborators:
        try:
            if isinstance(media, TV):
                _sync_tv(media, collab_user)
            elif isinstance(media, Season):
                _sync_season(media, collab_user)
            else:
                _sync_generic_media(media, model_class, collab_user)

            cache_utils.clear_time_left_cache_for_user(collab_user.id)
            cache_utils.clear_home_row_cache_for_user(collab_user.id)
            cache_utils.clear_media_list_cache_for_user(collab_user.id)
        except Exception:
            logger.exception(
                "Failed to sync media completion to collaborator user_id=%s for item_id=%s",
                collab_user.id,
                media.item_id,
            )


def _sync_tv(media, collab_user):
    """Sync completed TV show to collaborator."""
    collab_tv = TV.objects.filter(user=collab_user, item=media.item).first()
    if collab_tv:
        if collab_tv.status != Status.COMPLETED.value:
            collab_tv.status = Status.COMPLETED.value
            collab_tv.save()
    else:
        TV.objects.create(
            user=collab_user,
            item=media.item,
            status=Status.COMPLETED.value,
            notes="",
            score=None,
        )


def _sync_season(media, collab_user):
    """Sync completed Season to collaborator."""
    collab_season = Season.objects.filter(user=collab_user, item=media.item).first()
    if collab_season:
        if collab_season.status != Status.COMPLETED.value:
            collab_season.status = Status.COMPLETED.value
            collab_season.save()
    else:
        collab_season = Season(
            user=collab_user,
            item=media.item,
            status=Status.COMPLETED.value,
            notes="",
            score=None,
        )
        collab_season.save()


def _sync_generic_media(media, model_class, collab_user):
    """Sync completed Movie, Book, Game, Comic, Manga, Anime, etc. to collaborator."""
    field_names = {f.name for f in model_class._meta.fields}
    collab_media = model_class.objects.filter(user=collab_user, item=media.item).first()
    if collab_media:
        if collab_media.status != Status.COMPLETED.value:
            collab_media.status = Status.COMPLETED.value
            if "end_date" in field_names and not collab_media.end_date:
                collab_media.end_date = (
                    getattr(media, "end_date", None) or timezone.now()
                )
            if (
                "start_date" in field_names
                and not collab_media.start_date
                and getattr(media, "start_date", None)
            ):
                collab_media.start_date = media.start_date
            if "progress" in field_names and getattr(media, "progress", None):
                collab_media.progress = media.progress
            collab_media.save()
    else:
        create_kwargs = {
            "user": collab_user,
            "item": media.item,
            "status": Status.COMPLETED.value,
            "notes": "",
            "score": None,
        }
        if "start_date" in field_names and getattr(media, "start_date", None):
            create_kwargs["start_date"] = media.start_date
        if "end_date" in field_names:
            create_kwargs["end_date"] = (
                getattr(media, "end_date", None) or timezone.now()
            )
        if "progress" in field_names and getattr(media, "progress", None):
            create_kwargs["progress"] = media.progress
        model_class.objects.create(**create_kwargs)


def sync_episode_to_list_collaborators(episode, source_user):
    """Automatically sync episode completion to collaborators on shared lists."""
    if not episode or not hasattr(episode, "item") or not episode.item:
        return
    if not source_user or not getattr(source_user, "is_authenticated", False):
        return

    status = getattr(episode, "status", None)
    if status is None and getattr(episode, "dropped", False):
        return
    if status is not None and status != Status.COMPLETED.value:
        return

    collaborators = get_collaborators_for_item(episode.item, source_user)
    if not collaborators:
        return

    for collab_user in collaborators:
        try:
            library_media_type = (
                episode.item.library_media_type
                if getattr(episode.item, "library_media_type", "")
                not in ("", MediaTypes.EPISODE.value, MediaTypes.SEASON.value)
                else ""
            )
            collab_season = fork_services_episode.resolve_or_create_season(
                user=collab_user,
                media_id=episode.item.media_id,
                source=episode.item.source,
                season_number=episode.item.season_number,
                library_media_type=library_media_type,
            )
            already_watched = False
            if collab_season and collab_season.pk:
                already_watched = Episode.objects.filter(
                    related_season_id=collab_season.pk,
                    item=episode.item,
                    status=Status.COMPLETED.value,
                ).exists()
            if not already_watched:
                with contextlib.suppress(
                    fork_services_episode.EpisodeWatchConflictError
                ):
                    fork_services_episode.create_episode_watch(
                        collab_season,
                        episode.item,
                        end_date=episode.end_date or timezone.now(),
                        status=Status.COMPLETED.value,
                        score=None,
                        notes="",
                    )
                    if getattr(collab_season, "pk", None) and hasattr(
                        collab_season, "_sync_status_after_episode_change"
                    ):
                        collab_season._sync_status_after_episode_change()

            cache_utils.clear_time_left_cache_for_user(collab_user.id)
            cache_utils.clear_home_row_cache_for_user(collab_user.id)
            cache_utils.clear_media_list_cache_for_user(collab_user.id)
        except Exception:
            logger.exception(
                "Failed to sync episode watch to collaborator user_id=%s for item_id=%s",
                collab_user.id,
                episode.item_id,
            )
