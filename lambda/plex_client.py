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
            stream_url = f"{self.base_url}/audio/{part_id}.mp3"
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
