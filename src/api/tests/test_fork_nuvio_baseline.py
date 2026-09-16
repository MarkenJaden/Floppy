# FORK: baseline contract and regression tests for Nuvio / Stremio integration (PR 0).
from http import HTTPStatus as HTTP  # noqa: N814
from unittest.mock import patch

from app.models import Movie, PlaybackProgress, Status

from .base import FloppyApiTestCase


class NuvioContractBaselineTests(FloppyApiTestCase):
    """Verifies baseline scrobble, playback progress, and user isolation contracts."""

    def test_legacy_token_authentication(self):
        """Legacy User.token authenticates successfully against scrobble and playback endpoints."""
        response = self.call_api(
            "post",
            "api_scrobble",
            payload={"action": "start", "media_type": "movie", "ids": {"tmdb": "603"}},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)

    def test_scrobble_start_and_pause_are_non_durable(self):
        """'start' and 'pause' actions update the live playback state only, creating no DB history."""
        start_res = self.call_api(
            "post",
            "api_scrobble",
            payload={
                "action": "start",
                "media_type": "movie",
                "ids": {"tmdb": "603"},
                "position_seconds": 120,
                "duration_seconds": 7200,
            },
            headers=self.auth_headers,
        )
        self.assertEqual(start_res.status_code, HTTP.OK)
        self.assertFalse(
            Movie.objects.filter(user=self.user1, item__media_id="603").exists()
        )

        pause_res = self.call_api(
            "post",
            "api_scrobble",
            payload={
                "action": "pause",
                "media_type": "movie",
                "ids": {"tmdb": "603"},
                "position_seconds": 120,
            },
            headers=self.auth_headers,
        )
        self.assertEqual(pause_res.status_code, HTTP.OK)
        self.assertFalse(
            Movie.objects.filter(user=self.user1, item__media_id="603").exists()
        )

    def test_scrobble_stop_completed_creates_durable_history(self):
        """A completed stop event creates a completed watch history record."""

        def fake_process(self_proc, payload, user):
            movie_item = self.items_by_type["movie"][0]
            Movie.objects.get_or_create(
                user=user, item=movie_item, defaults={"status": Status.COMPLETED.value}
            )

        with patch(
            "integrations.webhooks.generic_scrobble.GenericScrobbleProcessor.process_payload",
            new=fake_process,
        ):
            mock_item = self.items_by_type["movie"][0]
            response = self.call_api(
                "post",
                "api_scrobble",
                payload={
                    "action": "stop",
                    "media_type": "movie",
                    "ids": {"tmdb": mock_item.media_id},
                    "completed": True,
                },
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, HTTP.OK)
            self.assertTrue(
                Movie.objects.filter(
                    user=self.user1, item=mock_item, status=Status.COMPLETED.value
                ).exists()
            )

    def test_playback_progress_per_user_isolation(self):
        """User A's playback progress cannot be seen or modified by User B."""
        movie_item = self.items_by_type["movie"][0]

        # User 1 sets progress to 500s
        res1 = self.call_api(
            "put",
            "api_playback_progress",
            payload={
                "media_type": "movie",
                "ids": {"tmdb": movie_item.media_id},
                "position_seconds": 500,
            },
            headers=self.auth_headers,
        )
        self.assertEqual(res1.status_code, HTTP.OK)

        # User 2 sets progress to 1500s
        res2 = self.call_api(
            "put",
            "api_playback_progress",
            payload={
                "media_type": "movie",
                "ids": {"tmdb": movie_item.media_id},
                "position_seconds": 1500,
            },
            headers=self.auth_headers2,
        )
        self.assertEqual(res2.status_code, HTTP.OK)

        # Verify DB records remain completely separate
        p1 = PlaybackProgress.objects.get(user=self.user1, item=movie_item)
        p2 = PlaybackProgress.objects.get(user=self.user2, item=movie_item)
        self.assertEqual(p1.position_seconds, 500)
        self.assertEqual(p2.position_seconds, 1500)

    def test_unresolvable_media_id_returns_404_not_500(self):
        """An unknown or invalid external identifier returns 404 cleanly without server error."""
        with patch("api.fork_views_playback.app.providers.tmdb.find", return_value={}):
            response = self.call_api(
                "put",
                "api_playback_progress",
                payload={
                    "media_type": "movie",
                    "ids": {"imdb": "tt-completely-nonexistent-id"},
                    "position_seconds": 100,
                },
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, HTTP.NOT_FOUND)
