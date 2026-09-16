"""Refresh TV calendar events that may contain fabricated provider times."""

from collections import defaultdict
from datetime import UTC

from django.core.management.base import BaseCommand, CommandError
from django.db.models.functions import ExtractHour, ExtractMinute, ExtractSecond

from app.models import TV, MediaTypes, Sources
from events.calendar.main import (
    cleanup_invalid_events,
    record_calendar_checks,
    save_events,
)
from events.calendar.tv import process_tv
from events.models import Event


class Command(BaseCommand):
    """Report or repair TMDB TV events with unreliable provider dates."""

    help = (
        "Refresh tracked TMDB TV seasons whose events use fabricated or "
        "mismatched provider dates"
    )

    def add_arguments(self, parser):
        """Add command-line filters and the write switch."""
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply provider-derived event updates (default is read-only)",
        )
        parser.add_argument(
            "--media-id",
            help="Only process the specified TMDB TV media id",
        )
        parser.add_argument(
            "--season-number",
            type=int,
            action="append",
            dest="season_numbers",
            help=(
                "Explicitly refresh this season number; may be repeated and "
                "requires --media-id"
            ),
        )
        parser.add_argument(
            "--username",
            help="Only process shows tracked by this user",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Limit the number of TV shows processed",
        )

    def handle(self, *_args, **options):
        """Report candidates or refresh their affected seasons."""
        if options["season_numbers"] and not options["media_id"]:
            message = "--season-number requires --media-id"
            raise CommandError(message)

        candidates = self._find_candidates(
            media_id=options["media_id"],
            username=options["username"],
            season_numbers=options["season_numbers"],
        )
        if options["limit"] is not None:
            candidates = candidates[: options["limit"]]

        if not candidates:
            self.stdout.write("No candidate TV calendar events found.")
            return

        mode = "APPLYING" if options["apply"] else "DRY RUN (no changes written)"
        self.stdout.write(f"{mode} - {len(candidates)} TV show(s) to check\n")
        for tv_item, season_numbers in candidates:
            self.stdout.write(self._label(tv_item, season_numbers))

        if not options["apply"]:
            self.stdout.write("\nRe-run with --apply to refresh these seasons.")
            return

        counts = {"updated": 0, "unchanged": 0, "failed": 0}
        for tv_item, season_numbers in candidates:
            outcome, changed_count = self._repair_candidate(
                tv_item,
                season_numbers,
            )
            counts[outcome] += 1
            self.stdout.write(
                f"{self._label(tv_item, season_numbers)}: "
                f"{changed_count} event(s) changed ({outcome})",
            )

        self.stdout.write(
            "\nUpdated: {updated}  Unchanged: {unchanged}  Failed: {failed}".format(
                **counts
            ),
        )

    def _find_candidates(
        self,
        *,
        media_id=None,
        username=None,
        season_numbers=None,
    ):
        """Return tracked TV items grouped with their affected seasons."""
        if season_numbers:
            return self._find_explicit_candidates(
                media_id=media_id,
                username=username,
                season_numbers=season_numbers,
            )

        event_queryset = (
            Event.objects.filter(
                item__media_type=MediaTypes.SEASON.value,
                item__source=Sources.TMDB.value,
            )
            .annotate(
                datetime_utc_hour=ExtractHour("datetime", tzinfo=UTC),
                datetime_utc_minute=ExtractMinute("datetime", tzinfo=UTC),
                datetime_utc_second=ExtractSecond("datetime", tzinfo=UTC),
            )
            .filter(
                datetime_utc_hour=12,
                datetime_utc_minute=0,
                datetime_utc_second=0,
            )
            .select_related("item")
        )
        if media_id:
            event_queryset = event_queryset.filter(item__media_id=media_id)

        season_numbers_by_media_id = defaultdict(set)
        for event in event_queryset:
            if event.datetime.microsecond:
                continue
            season_numbers_by_media_id[event.item.media_id].add(
                event.item.season_number,
            )

        tv_queryset = TV.objects.filter(
            item__media_type=MediaTypes.TV.value,
            item__source=Sources.TMDB.value,
            item__media_id__in=season_numbers_by_media_id.keys(),
        ).select_related("item")
        if username:
            tv_queryset = tv_queryset.filter(user__username=username)

        candidates = {}
        for tv in tv_queryset.order_by("item_id", "id"):
            season_numbers = sorted(
                season_number
                for season_number in season_numbers_by_media_id[tv.item.media_id]
                if season_number is not None
            )
            if season_numbers and tv.item_id not in candidates:
                candidates[tv.item_id] = (tv.item, season_numbers)

        return sorted(
            candidates.values(),
            key=lambda candidate: (candidate[0].title, candidate[0].media_id),
        )

    def _find_explicit_candidates(self, *, media_id, username, season_numbers):
        """Return explicitly requested tracked TV seasons regardless of dates."""
        requested_seasons = sorted(set(season_numbers))
        tv_queryset = TV.objects.filter(
            item__media_type=MediaTypes.TV.value,
            item__source=Sources.TMDB.value,
            item__media_id=media_id,
        ).select_related("item")
        if username:
            tv_queryset = tv_queryset.filter(user__username=username)

        candidates = {}
        for tv in tv_queryset.order_by("item_id", "id"):
            if tv.item_id not in candidates:
                candidates[tv.item_id] = (tv.item, requested_seasons)

        return sorted(
            candidates.values(),
            key=lambda candidate: (candidate[0].title, candidate[0].media_id),
        )

    def _repair_candidate(self, tv_item, season_numbers):
        """Refresh one TV item's affected seasons through the normal pipeline."""
        events_bulk = []
        previous_events = {
            (event.item_id, event.content_number): event.datetime
            for event in Event.objects.filter(
                item__media_id=tv_item.media_id,
                item__source=Sources.TMDB.value,
                item__media_type=MediaTypes.SEASON.value,
                item__season_number__in=season_numbers,
            )
        }

        if not process_tv(
            tv_item,
            events_bulk,
            force_seasons=season_numbers,
        ):
            return "failed", 0

        changed_count = sum(
            previous_events.get((event.item_id, event.content_number)) != event.datetime
            for event in events_bulk
        )
        save_events(events_bulk)
        cleanup_invalid_events(events_bulk)
        record_calendar_checks([tv_item])
        return ("updated" if changed_count else "unchanged"), changed_count

    @staticmethod
    def _label(tv_item, season_numbers):
        """Return a concise candidate label for command output."""
        seasons = ", ".join(f"S{season}" for season in season_numbers)
        return f"- {tv_item.title} ({tv_item.media_id}): {seasons}"
