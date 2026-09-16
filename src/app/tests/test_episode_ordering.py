from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import TV, Episode, Item, MediaTypes, Season, Sources, Status
from app.services.episode_ordering import apply_change, persist_order, preview_change


class EpisodeOrderingTests(TestCase):
    """Verify stable-identity previews and transactional order changes."""

    def setUp(self):
        """Create one show with a legacy episode watch."""
        self.user = get_user_model().objects.create_user(username="order-user")
        self.show = Item.objects.create(
            media_id="1396", source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value, title="Breaking Bad",
        )
        self.tv = TV.objects.create(
            item=self.show, user=self.user, status=Status.IN_PROGRESS.value,
        )
        self.season_item = Item.objects.create(
            media_id="1396", source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value, season_number=1,
            title="Breaking Bad",
        )
        self.season = Season.objects.create(
            item=self.season_item, related_tv=self.tv, user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        self.episode_item = Item.objects.create(
            media_id="1396", source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value, season_number=1,
            episode_number=1, title="Pilot", provider_episode_id="legacy-1",
        )
        self.watch = Episode.objects.create(
            item=self.episode_item, related_season=self.season,
            end_date=timezone.now(),
        )
        self.order = persist_order(
            self.show, Sources.TMDB.value, "1396", "aired", "TMDB (Aired)",
            {"episodes": [{
                "provider_episode_id": "new-1", "season_number": 1,
                "episode_number": 1, "title": "Pilot", "image": "",
            }]},
        )

    def test_preview_does_not_match_equal_coordinates(self):
        """Coordinates alone remain unresolved when provider identity differs."""
        preview = preview_change(self.tv, self.order)

        self.assertEqual(preview["resolutions"][0]["episode_ids"], [])

    def test_apply_requires_explicit_resolution_and_switches_active_order(self):
        """An explicit stable identity moves the watch and archives old rows."""
        preview = preview_change(self.tv, self.order)

        with self.assertRaises(ValueError):
            apply_change(
                self.tv, self.order, token=preview["token"], resolutions=[],
            )

        journal = apply_change(
            self.tv, self.order, token=preview["token"], resolutions=[{
                "watch_ids": [self.watch.pk], "episode_ids": ["new-1"],
                "archive": False,
            }],
        )

        self.assertEqual(journal.order_id, self.order.pk)
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.active_episode_order_id, self.order.pk)
        self.watch.refresh_from_db()
        self.assertFalse(self.watch.order_archived)
        self.assertEqual(self.watch.item.provider_episode_id, "new-1")
        self.assertTrue(Season.all_objects.get(pk=self.season.pk).order_archived)
        self.assertTrue(
            Season.objects.filter(
                item__episode_order=self.order,
                item__season_number=1,
            ).exists(),
        )
