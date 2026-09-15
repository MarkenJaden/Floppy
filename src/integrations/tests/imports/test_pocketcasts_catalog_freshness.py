"""Regressions for the Pocket Casts catalog re-walk.

Production evidence (2026-09-14): four recurring Pocket Casts runs of roughly
1,019 seconds each - about 30% of all background worker time over nine hours -
walked ~11,000 catalog episodes across 12 shows and imported nothing. Every
episode cost one SELECT, one hydrated model, and a field-by-field comparison,
on every poll, whether or not anything upstream had changed.

The skip is only safe if "unchanged" means exactly what ``_sync_catalog_episode``
would have written, so the central test here is a consistency test: for every
writable catalog field, a skip must imply the sync writes nothing, and a change
must imply the sync writes it.
"""

from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from app.models import PodcastEpisode, PodcastShow
from integrations.imports.pocketcasts import PocketCastsImporter
from integrations.models import PocketCastsAccount

PUBLISHED = datetime(2025, 1, 1, tzinfo=UTC)


def _catalog_payload(**overrides):
    """The shape _build_catalog_episode_data() produces."""
    payload = {
        "uuid": "uuid-1",
        "podcastUuid": "show-1",
        "podcastTitle": "Show",
        "author": "",
        "podcastSlug": "",
        "title": "Episode One",
        "slug": "episode-one",
        "published": int(PUBLISHED.timestamp()),
        "url": "https://example.com/1.mp3",
        "fileType": "audio/mpeg",
        "duration": 1800,
        "episodeType": "full",
        "episodeSeason": 2,
        "episodeNumber": 7,
        "isDeleted": False,
    }
    payload.update(overrides)
    return payload


class PocketCastsCatalogFreshnessTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="catalogtester",
            password="pass",  # test-only credential
        )
        PocketCastsAccount.objects.create(user=self.user, access_token="token")
        self.importer = PocketCastsImporter(self.user, "new")
        self.show = PodcastShow.objects.create(podcast_uuid="show-1", title="Show")
        self.episode = PodcastEpisode.objects.create(
            show=self.show,
            episode_uuid="uuid-1",
            title="Episode One",
            slug="episode-one",
            published=PUBLISHED,
            duration=1800,
            audio_url="https://example.com/1.mp3",
            episode_number=7,
            season_number=2,
            file_type="audio/mpeg",
            episode_type="full",
            is_deleted=False,
        )

    def _index(self):
        return self.importer._load_catalog_index(self.show)

    def _sync_writes_something(self, payload):
        """Run the real sync and report whether it changed the stored row."""
        before = PodcastEpisode.objects.filter(pk=self.episode.pk).values().first()
        count_before = PodcastEpisode.objects.count()
        self.importer._sync_catalog_episode(payload, show=self.show)
        after = PodcastEpisode.objects.filter(pk=self.episode.pk).values().first()
        return before != after or PodcastEpisode.objects.count() != count_before

    def test_identical_payload_is_unchanged(self):
        self.assertTrue(
            self.importer._catalog_episode_unchanged(_catalog_payload(), self._index()),
        )

    def test_unknown_episode_is_never_skipped(self):
        payload = _catalog_payload(uuid="uuid-brand-new")
        self.assertFalse(
            self.importer._catalog_episode_unchanged(payload, self._index()),
        )

    def test_reading_the_index_costs_one_query_per_show(self):
        for index in range(50):
            PodcastEpisode.objects.create(
                show=self.show,
                episode_uuid=f"bulk-{index}",
                title=f"Bulk {index}",
            )

        with self.assertNumQueries(1):
            catalog_index = self.importer._load_catalog_index(self.show)
        self.assertEqual(len(catalog_index), 51)

    def test_skipping_an_unchanged_episode_costs_no_queries(self):
        """The whole point: a settled catalog does no per-episode database work."""
        catalog_index = self._index()
        payload = _catalog_payload()

        with self.assertNumQueries(0):
            self.assertTrue(
                self.importer._catalog_episode_unchanged(payload, catalog_index),
            )

    def test_skip_and_sync_agree_field_by_field(self):
        """A skip must mean the sync would write nothing, for every field."""
        changes = {
            "title": "Episode One (Rebroadcast)",
            "slug": "episode-one-rebroadcast",
            "duration": 2400,
            "url": "https://example.com/1-remastered.mp3",
            "episodeNumber": 8,
            "episodeSeason": 3,
            "fileType": "audio/aac",
            "episodeType": "trailer",
            "isDeleted": True,
            "published": int(datetime(2025, 2, 1, tzinfo=UTC).timestamp()),
        }

        for key, value in changes.items():
            with self.subTest(field=key):
                payload = _catalog_payload(**{key: value})
                unchanged = self.importer._catalog_episode_unchanged(
                    payload,
                    self._index(),
                )
                self.assertFalse(
                    unchanged,
                    f"{key} changed but the episode was reported unchanged",
                )
                self.assertTrue(
                    self._sync_writes_something(payload),
                    f"{key} was reported changed but the sync wrote nothing",
                )
                # Put it back for the next field.
                self.importer._sync_catalog_episode(_catalog_payload(), show=self.show)
                self.episode.refresh_from_db()

    def test_a_skip_really_would_have_been_a_no_op(self):
        payload = _catalog_payload()
        self.assertTrue(
            self.importer._catalog_episode_unchanged(payload, self._index()),
        )
        self.assertFalse(self._sync_writes_something(payload))

    def test_blank_incoming_values_do_not_count_as_changes(self):
        """The sync never overwrites a stored value with a blank one."""
        payload = _catalog_payload(title="", url="", duration=0)
        self.assertTrue(
            self.importer._catalog_episode_unchanged(payload, self._index()),
        )
        self.assertFalse(self._sync_writes_something(payload))

    def test_unparseable_published_is_not_a_change(self):
        payload = _catalog_payload(published="not a date")
        self.assertTrue(
            self.importer._catalog_episode_unchanged(payload, self._index()),
        )
        self.assertFalse(self._sync_writes_something(payload))

    def test_negative_episode_numbers_are_not_a_change(self):
        """Provider sentinels coerce to unknown and must not force a write."""
        payload = _catalog_payload(episodeNumber=-1, episodeSeason=-1)
        self.assertTrue(
            self.importer._catalog_episode_unchanged(payload, self._index()),
        )
        self.assertFalse(self._sync_writes_something(payload))


class PocketCastsRepeatPollTests(TestCase):
    """An unchanged catalog must get cheaper on the second poll, end to end."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="pollster",
            password="pass",  # test-only credential
        )
        PocketCastsAccount.objects.create(user=self.user, access_token="token")

        self.podcast_list = {
            "podcasts": [
                {
                    "uuid": "show-1",
                    "title": "Test Show",
                    "author": "Test Author",
                    "description": "",
                    "url": "",
                },
            ],
        }
        self.metadata = {
            f"uuid-{index}": {
                "uuid": f"uuid-{index}",
                "title": f"Episode {index}",
                "published": PUBLISHED.isoformat(),
                "duration": 1800,
                "url": f"https://example.com/{index}.mp3",
            }
            for index in range(40)
        }

    def _artwork_patches(self):
        stack = ExitStack()
        stack.enter_context(
            patch(
                "integrations.pocketcasts_artwork.fetch_podcast_artwork_and_rss",
                return_value=(None, None),
            ),
        )
        stack.enter_context(
            patch(
                "integrations.pocketcasts_artwork.fetch_podcast_artwork",
                return_value=None,
            ),
        )
        return stack

    def _run_import(self):
        with (
            self._artwork_patches(),
            patch.object(PocketCastsImporter, "_ensure_valid_token"),
            patch.object(PocketCastsImporter, "_get_access_token", return_value="fake"),
            patch(
                "integrations.pocketcasts_api.get_podcast_list",
                return_value=self.podcast_list,
            ),
            patch.object(
                PocketCastsImporter,
                "_fetch_show_play_states",
                return_value={},
            ),
            patch.object(
                PocketCastsImporter,
                "_fetch_show_full_metadata",
                return_value=self.metadata,
            ),
            CaptureQueriesContext(connection) as queries,
        ):
            PocketCastsImporter(self.user, "new").import_data()
        return len(queries)

    def test_second_poll_of_an_unchanged_catalog_is_far_cheaper(self):
        first = self._run_import()
        second = self._run_import()

        self.assertEqual(PodcastEpisode.objects.count(), 40)
        # The second poll reads the catalog once and writes nothing. The exact
        # numbers are not the contract - the collapse is.
        self.assertLess(second * 4, first)
