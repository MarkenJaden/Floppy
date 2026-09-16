from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from app.providers import musicbrainz


class MusicBrainzRequestTests(SimpleTestCase):
    """Test MusicBrainz request configuration."""

    @override_settings(MUSICBRAINZ_URL="http://musicbrainz:5000/ws/2/")
    def test_mb_request_uses_custom_instance_url(self):
        with (
            patch("app.providers.musicbrainz._rate_limit") as mock_rate_limit,
            patch(
                "app.providers.musicbrainz.services.api_request",
                return_value={"id": "artist-mbid"},
            ) as mock_api_request,
        ):
            data = musicbrainz._mb_request("artist/artist-mbid", {"inc": "genres"})

        self.assertEqual(data, {"id": "artist-mbid"})
        mock_rate_limit.assert_called_once_with()
        self.assertEqual(
            mock_api_request.call_args.args[2],
            "http://musicbrainz:5000/ws/2/artist/artist-mbid",
        )
        self.assertEqual(
            mock_api_request.call_args.kwargs["params"],
            {"inc": "genres", "fmt": "json"},
        )


class MusicBrainzReleaseTests(SimpleTestCase):
    """Test release metadata formatting."""

    def test_capitalize_genre_handles_plain_hyphenated_and_acronym_values(self):
        self.assertEqual(musicbrainz.capitalize_genre("krautrock"), "Krautrock")
        self.assertEqual(
            musicbrainz.capitalize_genre("post-industrial"), "Post-Industrial"
        )
        self.assertEqual(musicbrainz.capitalize_genre("idm"), "IDM")
        self.assertEqual(musicbrainz.capitalize_genre("post-idm"), "Post-IDM")

    def test_get_release_returns_structured_artist_credits(self):
        """Release details should preserve individual artist credits."""
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch("app.providers.musicbrainz._mb_request") as mock_mb_request,
        ):
            mock_mb_request.return_value = {
                "title": "Shared Album",
                "date": "2024-01-15",
                "release-group": {"id": "release-group-mbid"},
                "artist-credit": [
                    {
                        "name": "Artist One",
                        "joinphrase": " & ",
                        "artist": {
                            "id": "artist-one-mbid",
                            "name": "Artist One",
                            "sort-name": "One, Artist",
                        },
                    },
                    {
                        "name": "Artist Two",
                        "joinphrase": "",
                        "artist": {
                            "id": "artist-two-mbid",
                            "name": "Artist Two",
                            "sort-name": "Two, Artist",
                        },
                    },
                ],
                "media": [],
            }

            data = musicbrainz.get_release("release-mbid", skip_cover_art=True)

        self.assertEqual(data["artist_name"], "Artist One & Artist Two")
        self.assertEqual(
            data["artist_credits"],
            [
                {
                    "artist_id": "artist-one-mbid",
                    "name": "Artist One",
                    "sort_name": "One, Artist",
                    "join_phrase": " & ",
                },
                {
                    "artist_id": "artist-two-mbid",
                    "name": "Artist Two",
                    "sort_name": "Two, Artist",
                    "join_phrase": "",
                },
            ],
        )

    def test_get_release_capitalizes_genres(self):
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch("app.providers.musicbrainz._mb_request") as mock_mb_request,
        ):
            mock_mb_request.return_value = {
                "title": "Genre Album",
                "date": "2024-01-15",
                "release-group": {"id": "release-group-mbid"},
                "artist-credit": [],
                "genres": [{"name": "post-idm"}, {"name": "krautrock"}],
                "media": [],
            }

            data = musicbrainz.get_release("release-mbid", skip_cover_art=True)

        self.assertEqual(data["genres"], ["Post-IDM", "Krautrock"])

    def test_get_release_group_genres_prefers_genres_then_tags(self):
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch("app.providers.musicbrainz._mb_request") as mock_mb_request,
        ):
            mock_mb_request.return_value = {
                "genres": [{"name": "post-rock"}],
                "tags": [{"name": "ignored"}],
            }

            data = musicbrainz.get_release_group_genres("rg-1")

        self.assertEqual(data, ["Post-Rock"])

    def test_get_genre_parents_walks_parent_chain_and_dedupes(self):
        def _mock_genre_request(endpoint, params=None):
            if endpoint == "genre":
                return {"genres": [{"id": "dubstep-id", "name": "dubstep"}]}
            if endpoint == "genre/dubstep-id":
                return {
                    "relations": [
                        {
                            "type": "subgenre of",
                            "direction": "forward",
                            "genre": {"id": "edm-id", "name": "edm"},
                        },
                    ],
                }
            if endpoint == "genre/edm-id":
                return {
                    "relations": [
                        {
                            "type": "subgenre of",
                            "direction": "forward",
                            "genre": {"id": "electronic-id", "name": "electronic"},
                        },
                    ],
                }
            if endpoint == "genre/electronic-id":
                return {"relations": []}
            raise AssertionError(endpoint)

        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch(
                "app.providers.musicbrainz._mb_request", side_effect=_mock_genre_request
            ),
        ):
            data = musicbrainz.get_genre_parents("Dubstep")

        self.assertEqual(data, ["EDM", "Electronic"])

    def test_get_genre_parents_falls_back_when_direction_missing(self):
        def _mock_genre_request(endpoint, params=None):
            if endpoint == "genre":
                return {"genres": [{"id": "art-rock-id", "name": "art rock"}]}
            if endpoint == "genre/art-rock-id":
                return {
                    "relations": [
                        {
                            "type": "subgenre of",
                            "genre": {"id": "rock-id", "name": "rock"},
                        },
                    ],
                }
            if endpoint == "genre/rock-id":
                return {"relations": []}
            raise AssertionError(endpoint)

        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch(
                "app.providers.musicbrainz._mb_request", side_effect=_mock_genre_request
            ),
        ):
            data = musicbrainz.get_genre_parents("Art Rock")

        self.assertEqual(data, ["Rock"])

    def test_get_genre_parents_infers_parents_when_genre_search_is_unavailable(self):
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch(
                "app.providers.musicbrainz._mb_request",
                side_effect=requests.exceptions.HTTPError(
                    response=type("Response", (), {"status_code": 501})(),
                ),
            ),
        ):
            self.assertEqual(
                musicbrainz.get_genre_parents("Experimental Rock"),
                ["Experimental", "Rock"],
            )
            self.assertEqual(
                musicbrainz.get_genre_parents("Plunderphonics"),
                ["Experimental"],
            )

    def test_get_genre_parents_caches_negative_lookup(self):
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set") as mock_cache_set,
            patch(
                "app.providers.musicbrainz._mb_request", return_value={"genres": []}
            ) as mock_mb_request,
        ):
            data = musicbrainz.get_genre_parents("Missing Genre")

        self.assertEqual(data, [])
        self.assertEqual(mock_mb_request.call_count, 1)
        cache_values = [call.args[1] for call in mock_cache_set.call_args_list]
        self.assertIn("", cache_values)
        self.assertIn([], cache_values)

    def test_get_release_normalizes_pressing_metadata(self):
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch("app.providers.musicbrainz._mb_request") as mock_mb_request,
        ):
            mock_mb_request.return_value = {
                "title": "Pressing Album",
                "date": "1977-06-01",
                "country": "US",
                "status": "official",
                "packaging": "Gatefold",
                "barcode": "0123456789012",
                "release-group": {"id": "release-group-mbid"},
                "artist-credit": [],
                "label-info": [
                    {
                        "catalog-number": "ABC-123",
                        "label": {"name": "Example Records"},
                    },
                ],
                "media": [
                    {"format": "Vinyl", "track-count": 5, "tracks": []},
                    {"format": "Vinyl", "track-count": 4, "tracks": []},
                ],
            }

            data = musicbrainz.get_release("release-mbid", skip_cover_art=True)

        self.assertEqual(data["country"], "US")
        self.assertEqual(data["format"], "Vinyl")
        self.assertEqual(data["label"], "Example Records")
        self.assertEqual(data["catalog_numbers"], ["ABC-123"])
        self.assertEqual(data["barcode"], "0123456789012")
        self.assertEqual(data["track_count"], 9)

    def test_get_release_group_releases_normalizes_and_sorts_results(self):
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set") as mock_cache_set,
            patch("app.providers.musicbrainz._mb_request") as mock_mb_request,
        ):
            mock_mb_request.return_value = {
                "release-count": 2,
                "releases": [
                    {
                        "id": "bootleg-release",
                        "title": "Live Bootleg",
                        "date": "2025",
                        "status": "bootleg",
                        "country": "DE",
                        "media": [{"format": "CD", "track-count": 8}],
                    },
                    {
                        "id": "official-release",
                        "title": "Original Pressing",
                        "date": "1977",
                        "status": "official",
                        "country": "US",
                        "media": [{"format": "Vinyl", "track-count": 9}],
                    },
                ],
            }

            releases = musicbrainz.get_release_group_releases("group-mbid")

        self.assertEqual(
            [release["release_id"] for release in releases],
            ["official-release", "bootleg-release"],
        )
        self.assertEqual(releases[0]["format"], "Vinyl")
        self.assertEqual(releases[0]["track_count"], 9)
        self.assertEqual(
            mock_mb_request.call_args.args[1],
            {
                "release-group": "group-mbid",
                "inc": "labels+media+release-groups",
                "limit": 100,
                "offset": 0,
            },
        )
        self.assertTrue(mock_cache_set.called)

    def test_get_release_for_group_prefers_release_with_most_tracks(self):
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set") as mock_cache_set,
            patch("app.providers.musicbrainz._mb_request") as mock_mb_request,
        ):
            mock_mb_request.return_value = {
                "releases": [
                    {
                        "id": "vinyl-reissue",
                        "status": "official",
                        "media": [{"format": "Vinyl", "track-count": 12}],
                    },
                    {
                        "id": "digital-original",
                        "status": "official",
                        "media": [{"format": "Digital Media", "track-count": 24}],
                    },
                ],
            }

            release_id = musicbrainz.get_release_for_group("group-mbid")

        self.assertEqual(release_id, "digital-original")
        mock_cache_set.assert_called_once_with(
            "musicbrainz_release_for_group_group-mbid",
            "digital-original",
            60 * 60 * 24 * 7,
        )


class MusicBrainzCombinedSearchTests(SimpleTestCase):
    """Test combined music search result formatting."""

    def test_page_one_uses_release_artwork_for_artists(self):
        """First page should populate artist artwork from matching release art."""
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch("app.providers.musicbrainz.search_artists") as mock_search_artists,
            patch("app.providers.musicbrainz.search_releases") as mock_search_releases,
            patch("app.providers.musicbrainz.search") as mock_search_tracks,
        ):
            mock_search_artists.return_value = {
                "results": [
                    {"artist_id": "artist-1", "name": "Pentatonix"},
                    {"artist_id": "artist-2", "name": "No Cover Artist"},
                ],
            }
            mock_search_releases.return_value = {
                "results": [
                    {
                        "release_id": "release-1",
                        "artist_id": "artist-1",
                        "artist_name": "Pentatonix",
                        "image": "http://example.com/cover.jpg",
                    },
                ],
            }
            mock_search_tracks.return_value = {
                "page": 1,
                "total_results": 0,
                "total_pages": 0,
                "results": [],
            }

            data = musicbrainz.search_combined("pentatonix", page=1)

            mock_search_artists.assert_called_once_with("pentatonix", page=1)
            mock_search_releases.assert_called_once_with(
                "pentatonix",
                page=1,
                skip_cover_art=True,
            )
            mock_search_tracks.assert_called_once_with(
                "pentatonix",
                page=1,
                skip_cover_art=True,
            )
            self.assertEqual(
                data["artists"][0]["image"], "http://example.com/cover.jpg"
            )
            self.assertEqual(data["artists"][1]["image"], settings.IMG_NONE)
            self.assertEqual(
                data["releases"][0]["image"], "http://example.com/cover.jpg"
            )

    def test_page_one_builds_async_cover_url_when_cover_missing(self):
        """First page should provide async cover URLs when no art is preloaded."""
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch("app.providers.musicbrainz.search_artists") as mock_search_artists,
            patch("app.providers.musicbrainz.search_releases") as mock_search_releases,
            patch("app.providers.musicbrainz.search") as mock_search_tracks,
        ):
            mock_search_artists.return_value = {
                "results": [{"artist_id": "artist-1", "name": "Pentatonix"}],
            }
            mock_search_releases.return_value = {
                "results": [
                    {
                        "release_id": "release-1",
                        "artist_id": "artist-1",
                        "artist_name": "Pentatonix",
                        "image": settings.IMG_NONE,
                    },
                ],
            }
            mock_search_tracks.return_value = {
                "page": 1,
                "total_results": 0,
                "total_pages": 0,
                "results": [],
            }

            data = musicbrainz.search_combined("pentatonix", page=1)

            expected_cover = f"{musicbrainz.COVER_ART_BASE}/release/release-1/front-250"
            self.assertEqual(data["releases"][0]["image"], expected_cover)
            self.assertEqual(data["artists"][0]["image"], expected_cover)

    def test_page_two_returns_tracks_only(self):
        """Subsequent pages should skip artist/album sections."""
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch("app.providers.musicbrainz.search_artists") as mock_search_artists,
            patch("app.providers.musicbrainz.search_releases") as mock_search_releases,
            patch("app.providers.musicbrainz.search") as mock_search_tracks,
        ):
            mock_search_tracks.return_value = {
                "page": 2,
                "total_results": 40,
                "total_pages": 2,
                "results": [{"media_id": "rec-1"}],
            }

            data = musicbrainz.search_combined("pentatonix", page=2)

            mock_search_artists.assert_not_called()
            mock_search_releases.assert_not_called()
            mock_search_tracks.assert_called_once_with(
                "pentatonix",
                page=2,
                skip_cover_art=True,
            )
            self.assertEqual(data["artists"], [])
            self.assertEqual(data["releases"], [])
            self.assertEqual(data["tracks"]["page"], 2)


class MusicBrainzWikipediaDataTests(SimpleTestCase):
    """Test disambiguation-page handling in get_wikipedia_data (issue #979)."""

    def test_disambiguation_page_is_treated_as_a_miss(self):
        mock_response = Mock(ok=True)
        mock_response.json.return_value = {
            "type": "disambiguation",
            "extract": "Tool may refer to: Tool, an implement...",
        }
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set") as mock_cache_set,
            patch("app.providers.musicbrainz.requests.get", return_value=mock_response),
        ):
            result = musicbrainz.get_wikipedia_data("Tool")

        self.assertIsNone(result["extract"])
        self.assertIsNone(result["image"])
        # Treated as a miss: cached with the shorter (1 day) miss TTL.
        mock_cache_set.assert_called_once()
        self.assertEqual(mock_cache_set.call_args.args[2], 60 * 60 * 24)

    def test_standard_page_extract_is_returned_unchanged(self):
        mock_response = Mock(ok=True)
        mock_response.json.return_value = {
            "type": "standard",
            "extract": "Tool is an American rock band.",
        }
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch("app.providers.musicbrainz.requests.get", return_value=mock_response),
        ):
            result = musicbrainz.get_wikipedia_data("Tool_(band)")

        self.assertEqual(result["extract"], "Tool is an American rock band.")


class MusicBrainzLastFmBioTests(SimpleTestCase):
    """Test get_lastfm_bio, the new primary (MBID-resolved) bio source."""

    # The Last.fm key resolves through the database (Settings > Metadata).
    databases = {"default"}

    @override_settings(LASTFM_API_KEY="")
    def test_returns_none_without_configured_api_key(self):
        with patch("app.providers.musicbrainz.requests.get") as mock_get:
            result = musicbrainz.get_lastfm_bio("mbid-1")

        self.assertIsNone(result)
        mock_get.assert_not_called()

    @override_settings(LASTFM_API_KEY="test-key")
    def test_strips_read_more_boilerplate_and_html(self):
        mock_response = Mock(ok=True)
        mock_response.json.return_value = {
            "artist": {
                "bio": {
                    "summary": (
                        "Tool is an American rock band. "
                        '<a href="https://www.last.fm/music/Tool">'
                        "Read more on Last.fm</a>."
                    ),
                },
            },
        }
        with (
            patch("app.providers.musicbrainz.cache.get", return_value="unset"),
            patch("app.providers.musicbrainz.cache.set"),
            patch(
                "app.providers.musicbrainz.requests.get", return_value=mock_response
            ) as mock_get,
        ):
            result = musicbrainz.get_lastfm_bio("mbid-1")

        self.assertEqual(result, "Tool is an American rock band.")
        self.assertEqual(mock_get.call_args.kwargs["params"]["mbid"], "mbid-1")


class MusicBrainzGetArtistBioTests(SimpleTestCase):
    """Test get_artist's bio-resolution priority order (issue #979)."""

    def _base_mb_response(self, **overrides):
        base = {
            "name": "Tool",
            "sort-name": "Tool",
            "disambiguation": "",
            "type": "Group",
            "country": "US",
            "life-span": {},
            "area": {},
            "annotation": "",
            "relations": [],
            "genres": [],
            "tags": [],
            "rating": {},
            "release-groups": [],
        }
        base.update(overrides)
        return base

    @override_settings(LASTFM_API_KEY="test-key")
    def test_uses_lastfm_bio_and_skips_wikipedia(self):
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch(
                "app.providers.musicbrainz._mb_request",
                return_value=self._base_mb_response(),
            ),
            patch(
                "app.providers.musicbrainz.get_lastfm_bio",
                return_value="Tool is an American rock band.",
            ) as mock_lastfm,
            patch("app.providers.musicbrainz.get_wikipedia_data") as mock_wiki,
        ):
            result = musicbrainz.get_artist("artist-1")

        mock_lastfm.assert_called_once_with("artist-1")
        mock_wiki.assert_not_called()
        self.assertEqual(result["bio"], "Tool is an American rock band.")

    def test_falls_back_to_band_guess_when_lastfm_unavailable(self):
        """No Last.fm bio and no MB wikipedia relation: try '{name} (band)'
        before the bare name, so a same-named disambiguation page isn't hit.
        """
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch(
                "app.providers.musicbrainz._mb_request",
                return_value=self._base_mb_response(),
            ),
            patch("app.providers.musicbrainz.get_lastfm_bio", return_value=None),
            patch("app.providers.musicbrainz.get_wikipedia_data") as mock_wiki,
        ):
            mock_wiki.return_value = {
                "extract": "Tool is an American rock band.",
                "image": None,
            }
            result = musicbrainz.get_artist("artist-1")

        mock_wiki.assert_called_once_with("Tool_(band)")
        self.assertEqual(result["bio"], "Tool is an American rock band.")

    def test_prefers_mb_wikipedia_relation_over_band_guess(self):
        response = self._base_mb_response(
            relations=[
                {
                    "type": "wikipedia",
                    "url": {
                        "resource": (
                            "https://en.wikipedia.org/wiki/Tool_(American_band)"
                        ),
                    },
                },
            ],
        )
        with (
            patch("app.providers.musicbrainz.cache.get", return_value=None),
            patch("app.providers.musicbrainz.cache.set"),
            patch("app.providers.musicbrainz._mb_request", return_value=response),
            patch("app.providers.musicbrainz.get_lastfm_bio", return_value=None),
            patch("app.providers.musicbrainz.get_wikipedia_data") as mock_wiki,
        ):
            mock_wiki.return_value = {
                "extract": "Tool is an American rock band.",
                "image": None,
            }
            musicbrainz.get_artist("artist-1")

        mock_wiki.assert_called_once_with("Tool_(American_band)")
