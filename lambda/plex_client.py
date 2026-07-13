"""Plex Media Server integration for music playback."""

import logging
import os
from urllib.parse import urlencode

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

logger = logging.getLogger(__name__)


class PlexMusicClient:
    """Client for interacting with Plex music libraries."""

    def __init__(self, base_url=None, token=None, library_name=None):
        self.base_url = base_url or os.environ["PLEX_URL"]
        self.token = token or os.environ["PLEX_TOKEN"]
        self.library_name = library_name or os.environ.get("PLEX_MUSIC_LIBRARY", "Music")
        self._server = None
        self._music_library = None

    @property
    def server(self):
        if self._server is None:
            self._server = PlexServer(self.base_url, self.token)
        return self._server

    @property
    def music_library(self):
        if self._music_library is None:
            self._music_library = self.server.library.section(self.library_name)
        return self._music_library

    def search_tracks(self, query):
        """Search for tracks by title."""
        results = self.music_library.searchTracks(title=query)
        return results

    def search_artist(self, artist_name):
        """Search for an artist and return their tracks."""
        results = self.music_library.searchArtists(title=artist_name)
        if not results:
            return []
        artist = results[0]
        return artist.tracks()

    def search_album(self, album_name, artist_name=None):
        """Search for an album and return its tracks."""
        if artist_name:
            artists = self.music_library.searchArtists(title=artist_name)
            if artists:
                albums = [a for a in artists[0].albums() if album_name.lower() in a.title.lower()]
                if albums:
                    return albums[0].tracks()

        results = self.music_library.searchAlbums(title=album_name)
        if not results:
            return []
        return results[0].tracks()

    def get_playlist(self, playlist_name):
        """Get tracks from a Plex playlist."""
        try:
            playlists = self.server.playlists()
            for playlist in playlists:
                if playlist_name.lower() in playlist.title.lower():
                    return playlist.items()
        except NotFound:
            pass
        return []

    def get_stream_url(self, track):
        """Build an HTTPS streaming URL for a track.

        Uses a clean URL pattern /audio/{partId}.mp3 which the Apache
        reverse proxy rewrites to the full Plex path with auth token.
        This avoids exposing the token in URLs sent to Alexa.
        """
        if track.media and track.media[0].parts:
            part = track.media[0].parts[0]
            part_id = part.id
            # DEBUG: serve from S3 to test if domain is the issue
            stream_url = "https://plex-music-test-YOUR-AWS-ACCOUNT-ID.s3.us-east-1.amazonaws.com/wasted-years.mp3?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIAXJ2OIVS64JT5F2MH%2F20260713%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260713T065131Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=host&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEC8aCXVzLWVhc3QtMSJGMEQCIBRv2u8pHd3tEpDf%2FjsiaC04qTtA%2Ba0SU5YNqspZs2R1AiB67apJ1dCF1UgvO2DV2df2ZH2FqBm9C4fqs%2B3Oj%2FHY0yqmBAj4%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F8BEAAaDDUwMjEzODQ0OTA4NSIMS07KA4CWhm1WesKpKvoDhl7gQ8bNvU858NYHMoSVXmDgUDoe%2FDYX38pcjBxFfoNw0uqrR5M8q%2BOAh7nHb7HFxP%2Bpd8blOU1cCo3uosAyZUnzfCr3Xdw2nMjYK%2FcsLZmu3KlNnEjhEsfsbhvqc1h4DyrIDUtJ81Ry2B5ZRckbVBYPjmeDH6wTK%2BUIvTv0NVj3ZxXXdrcHkcHgEtmZGCyjhEzHYcxADnkTfKcrUhvrJHwD7TWPYxNj9D27jLKHELXD%2B9cfse%2FixGjEr%2Fj2XgZBWBm4A9xArEXcfm0W3VdtkJ%2FaNoCOOTzOSrBOPX3qp6wzC11oMtI%2BQIpCgB%2B16anrQiMMlIoDOO4DONhJmqy8tGIdw8a69MlelKjmQTSi714XUe7BI1H%2BIfPAed0oPUkjyc4kdWfIXo44gYTRBi%2F26Rl%2Bk6eke%2Fewly9Mz94g3nfxOtv549OSTwZoabNhmmDq1S8Q8PEVGHq3NQLB53%2FSdVmWYG16s8koCTQ4ABokkzwBHEtfSYlc3Yc6SMA%2BOAy5WRk5goRiNvM%2F5y66dULX1tyjcotXDnaKpZkVmmh7jgVyvADnOAtf%2Fq5ll0owfvAOna5FSodcL5RtuvRwPV3LYV6bdvszQ75y3VaY5nDhvuLNtg3QPOj9BBaI2z0GGfW8wXswoBSGVqfGfNKH4iuTwfPCTuGRpozOgOMwhrbR0gY6sgKFcDALBtDAeo6gjGAZAOcpZsr3jVYq26Ek0835HAr%2Fwlst3TAIyqprbkek3BK7OAxmnqCT1CijTHrc%2BKRJ%2B2jBEEOuNkk8QBL1r5T7LP6EHskLgPpQ1QQxyn1icIP6K5i%2Brf1NdxP8%2Foh%2FqqjSGpSUgCZc73HGJXVvIJt39tpn9qczy7edxlCWvmSyq0yoF3IhlV6s0VkfhwpysuCKgb0thCQcprjpmTqvzXUZNjADRyNum0jdFwPep07zsGfJ9xD5Yg%2B8k0Ls0uDwtvGYhP8%2Fe706yx4BolUehfekVggTJ5WXl2fplAqTgd3hUC7NBAAdT8osMhwYeDTDfuulvlLDyjA3RdfXyf7A5NCy5UQwCMAFJqoT674rdB525euhErB8SQZi6sC7y9uFFSO60S26I1w%3D&X-Amz-Signature=81435e35ca225ccbb9a129d937176cb66434c4f6b371327b021192f11ce9a436"
            return stream_url

        # Fallback (shouldn't happen for music tracks)
        return f"{self.base_url}/audio/0.mp3"

    def get_track_info(self, track):
        """Extract metadata from a track for Alexa cards/speech."""
        return {
            "title": track.title,
            "artist": track.grandparentTitle or "Unknown Artist",
            "album": track.parentTitle or "Unknown Album",
            "duration_ms": track.duration or 0,
            "art_url": self._get_art_url(track),
        }

    def _get_art_url(self, track):
        """Get album art URL for display cards."""
        if track.thumb:
            return f"{self.base_url}{track.thumb}?X-Plex-Token={self.token}"
        return None
