from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import TV, Episode, Item, MediaTypes, Movie, Season, Sources, Status
from integrations.imports.tvtime import importer_movies, importer_shows

SHOWS_HEADER = "series_name,created_at,episode_id,season_number,episode_number"
MOVIES_HEADER = "movie_name,updated_at,type,release_date"


def _show_row(*, series_name, created_at, episode_id, season_number, episode_number):
    return f"{series_name},{created_at},{episode_id},{season_number},{episode_number}"


def _movie_row(*, movie_name, updated_at, row_type, release_date=""):
    return f"{movie_name},{updated_at},{row_type},{release_date}"


def _shows_csv(*rows):
    return (SHOWS_HEADER + "\n" + "\n".join(rows) + "\n").encode("utf-8")


def _movies_csv(*rows):
    return (MOVIES_HEADER + "\n" + "\n".join(rows) + "\n").encode("utf-8")


def _tv_metadata(title, last_episode_season=None):
    metadata = {"title": title, "image": "tv.jpg"}
    if last_episode_season is not None:
        metadata["last_episode_season"] = last_episode_season
    return metadata


def _season_metadata(episode_numbers):
    return {
        "title": "Season 1",
        "image": "season.jpg",
        "max_progress": len(episode_numbers),
        "episodes": [{"episode_number": n} for n in episode_numbers],
    }


class ImportTvTimeShows(TestCase):
    """Test importing episode watch history from a TV Time shows CSV."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    @patch("integrations.imports.trakt.services.search")
    @patch("integrations.imports.tvtime.TvTimeShowImporter._get_metadata")
    def test_episode_row_creates_item_and_episode(self, mock_get_metadata, mock_search):
        """A watched-episode row resolves the show by title and records the watch."""
        mock_search.return_value = {
            "results": [{"media_id": 111, "title": "Severance", "year": 2022}],
        }
        mock_get_metadata.side_effect = [
            _tv_metadata("Severance"),
            _season_metadata([1, 2, 3]),
        ]

        csv_file = BytesIO(
            _shows_csv(
                _show_row(
                    series_name="Severance",
                    created_at="2024-03-01T20:00:00Z",
                    episode_id="1",
                    season_number=1,
                    episode_number=2,
                ),
            ),
        )

        imported_counts, warnings = importer_shows(csv_file, self.user, "new")

        self.assertEqual(warnings, "")
        self.assertEqual(imported_counts.get(MediaTypes.EPISODE.value), 1)
        item = Item.objects.get(
            media_id="111",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
        )
        episode = Episode.objects.get(item=item)
        self.assertEqual(
            episode.end_date,
            datetime(2024, 3, 1, 20, 0, tzinfo=UTC),
        )
        tv = TV.objects.get(user=self.user, item__media_id="111")
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

    @patch("integrations.imports.trakt.services.search")
    @patch("integrations.imports.tvtime.TvTimeShowImporter._get_metadata")
    def test_last_episode_completes_season_and_show(
        self, mock_get_metadata, mock_search
    ):
        """Watching a season's last episode marks the season and show Completed."""
        mock_search.return_value = {
            "results": [{"media_id": 222, "title": "A Show", "year": 2020}],
        }
        mock_get_metadata.side_effect = [
            _tv_metadata("A Show", last_episode_season=1),
            _season_metadata([1, 2, 3]),
        ]

        csv_file = BytesIO(
            _shows_csv(
                _show_row(
                    series_name="A Show",
                    created_at="2024-01-01T12:00:00Z",
                    episode_id="1",
                    season_number=1,
                    episode_number=1,
                ),
                _show_row(
                    series_name="A Show",
                    created_at="2024-01-02T12:00:00Z",
                    episode_id="2",
                    season_number=1,
                    episode_number=2,
                ),
                _show_row(
                    series_name="A Show",
                    created_at="2024-01-03T12:00:00Z",
                    episode_id="3",
                    season_number=1,
                    episode_number=3,
                ),
            ),
        )

        importer_shows(csv_file, self.user, "new")

        season = Season.objects.get(user=self.user, item__media_id="222")
        self.assertEqual(season.status, Status.COMPLETED.value)
        tv = TV.objects.get(user=self.user, item__media_id="222")
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertEqual(Episode.objects.filter(related_season=season).count(), 3)

    @patch("integrations.imports.trakt.services.search")
    def test_unmatched_title_warns_and_does_not_raise(self, mock_search):
        """A show TMDB can't find produces a warning instead of crashing the import."""
        mock_search.return_value = {"results": []}

        csv_file = BytesIO(
            _shows_csv(
                _show_row(
                    series_name="No Such Show",
                    created_at="2024-01-01T00:00:00Z",
                    episode_id="1",
                    season_number=1,
                    episode_number=1,
                ),
            ),
        )

        imported_counts, warnings = importer_shows(csv_file, self.user, "new")

        self.assertEqual(imported_counts, {})
        self.assertIn("No Such Show", warnings)

    @patch("integrations.imports.trakt.services.search")
    @patch("integrations.imports.tvtime.TvTimeShowImporter._get_metadata")
    def test_episode_not_in_tmdb_season_warns_and_skips(
        self,
        mock_get_metadata,
        mock_search,
    ):
        """An episode number TMDB doesn't have for that season is skipped with a warning."""
        mock_search.return_value = {
            "results": [{"media_id": 333, "title": "A Show", "year": 2020}],
        }
        mock_get_metadata.side_effect = [
            _tv_metadata("A Show"),
            _season_metadata([1, 2]),
        ]

        csv_file = BytesIO(
            _shows_csv(
                _show_row(
                    series_name="A Show",
                    created_at="2024-01-01T00:00:00Z",
                    episode_id="1",
                    season_number=1,
                    episode_number=99,
                ),
            ),
        )

        imported_counts, warnings = importer_shows(csv_file, self.user, "new")

        self.assertEqual(imported_counts, {})
        self.assertIn("not found in", warnings)

    @patch("integrations.imports.trakt.services.search")
    @patch("integrations.imports.tvtime.TvTimeShowImporter._get_metadata")
    def test_reimporting_same_csv_is_a_noop(self, mock_get_metadata, mock_search):
        """Re-importing the same export in "new" mode doesn't duplicate the watch."""
        mock_search.return_value = {
            "results": [{"media_id": 444, "title": "A Show", "year": 2020}],
        }
        mock_get_metadata.side_effect = [
            _tv_metadata("A Show"),
            _season_metadata([1, 2, 3]),
            _tv_metadata("A Show"),
            _season_metadata([1, 2, 3]),
        ]
        row = _show_row(
            series_name="A Show",
            created_at="2024-01-01T00:00:00Z",
            episode_id="1",
            season_number=1,
            episode_number=1,
        )

        importer_shows(BytesIO(_shows_csv(row)), self.user, "new")
        imported_counts, _ = importer_shows(BytesIO(_shows_csv(row)), self.user, "new")

        self.assertEqual(imported_counts, {})
        self.assertEqual(Episode.objects.count(), 1)


class ImportTvTimeMovies(TestCase):
    """Test importing movie watch activity from a TV Time movies CSV."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    @patch("integrations.imports.trakt.services.search")
    @patch("integrations.imports.tvtime.TvTimeMovieImporter._get_metadata")
    def test_watched_movie_row_creates_movie(self, mock_get_metadata, mock_search):
        """A "watch" row creates a completed Movie with the right end_date."""
        mock_search.return_value = {
            "results": [{"media_id": 555, "title": "A Movie", "year": 2019}],
        }
        mock_get_metadata.return_value = {"title": "A Movie", "image": "movie.jpg"}

        csv_file = BytesIO(
            _movies_csv(
                _movie_row(
                    movie_name="A Movie",
                    updated_at="2024-02-01T10:00:00Z",
                    row_type="watch",
                    release_date="2019-01-01T00:00:00Z",
                ),
            ),
        )

        imported_counts, warnings = importer_movies(csv_file, self.user, "new")

        self.assertEqual(warnings, "")
        self.assertEqual(imported_counts.get(MediaTypes.MOVIE.value), 1)
        movie = Movie.objects.get(user=self.user, item__media_id="555")
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(
            movie.end_date,
            datetime(2024, 2, 1, 10, 0, tzinfo=UTC),
        )

    @patch("integrations.imports.trakt.services.search")
    def test_non_watch_row_is_skipped(self, mock_search):
        """A "want_to_watch" row isn't a completed watch and is ignored."""
        csv_file = BytesIO(
            _movies_csv(
                _movie_row(
                    movie_name="A Movie",
                    updated_at="2024-02-01T10:00:00Z",
                    row_type="want_to_watch",
                ),
            ),
        )

        imported_counts, warnings = importer_movies(csv_file, self.user, "new")

        self.assertEqual(imported_counts, {})
        self.assertEqual(warnings, "")
        mock_search.assert_not_called()

    @patch("integrations.imports.trakt.services.search")
    @patch("integrations.imports.tvtime.TvTimeMovieImporter._get_metadata")
    def test_new_mode_skips_already_tracked_movie(self, mock_get_metadata, mock_search):
        """ "new" mode leaves an already-tracked movie untouched."""
        mock_search.return_value = {
            "results": [{"media_id": 666, "title": "A Movie", "year": 2019}],
        }
        mock_get_metadata.return_value = {"title": "A Movie", "image": "movie.jpg"}
        item = Item.objects.create(
            media_id="666",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="A Movie",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
        )

        csv_file = BytesIO(
            _movies_csv(
                _movie_row(
                    movie_name="A Movie",
                    updated_at="2024-02-01T10:00:00Z",
                    row_type="watch",
                ),
            ),
        )

        imported_counts, _ = importer_movies(csv_file, self.user, "new")

        self.assertEqual(imported_counts, {})
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
