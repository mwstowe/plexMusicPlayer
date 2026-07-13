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
        self.stream_base_url = os.environ.get("STREAM_BASE_URL", self.base_url)
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
        """Search for tracks by title.

        Uses Plex's general search which matches against title,
        artist, and other metadata fields.
        """
        # Try exact title search first
        results = self.music_library.searchTracks(title=query)
        if results:
            return results

        # Fall back to general search which is more forgiving
        results = self.music_library.search(query, libtype="track")
        return results

    def search_artist(self, artist_name):
        """Search for an artist and return their tracks.

        Searches both library-level artists (grandparentTitle) and
        per-track original artists (originalTitle) for compilations
        and loose files where the folder artist differs from the
        actual track artist.
        """
        # First try library-level artist search
        results = self.music_library.searchArtists(title=artist_name)
        if results:
            artist = results[0]
            tracks = artist.tracks()
            if tracks:
                return tracks

        # Fall back to track-level search which matches originalTitle
        tracks = self.music_library.search(artist_name, libtype="track")
        return tracks

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

    def get_all_tracks(self):
        """Get all tracks in the music library for shuffle-all."""
        return self.music_library.searchTracks()

    def get_tracks_by_keys(self, rating_keys):
        """Fetch tracks by their rating keys (for queue restoration)."""
        tracks = []
        for key in rating_keys:
            try:
                results = self.server.fetchItems(f"/library/metadata/{key}")
                if results:
                    tracks.append(results[0])
            except Exception as e:
                logger.error("Failed to fetch track %s: %s", key, e)
                continue
        logger.info("get_tracks_by_keys: requested %d, got %d", len(rating_keys), len(tracks))
        return tracks

    def get_stream_url(self, track):
        """Build an HTTPS streaming URL for a track.

        Uses CloudFront (STREAM_BASE_URL) as the CDN in front of Plex.
        CloudFront connects to Plex on port 32400, and the Plex token
        is passed as a query parameter for authentication.
        """
        if track.media and track.media[0].parts:
            part = track.media[0].parts[0]
            stream_url = f"{self.stream_base_url}{part.key}?X-Plex-Token={self.token}"
            return stream_url

        # Fallback (shouldn't happen for music tracks)
        return f"{self.stream_base_url}{track.key}?X-Plex-Token={self.token}"

    def get_track_info(self, track):
        """Extract metadata from a track for Alexa cards/speech."""
        # Prefer originalTitle (per-track artist from ID3 tags) over
        # grandparentTitle (library folder artist, often "Various Artists")
        artist = track.originalTitle or track.grandparentTitle or "Unknown Artist"
        return {
            "title": track.title,
            "artist": artist,
            "album": track.parentTitle or "Unknown Album",
            "duration_ms": track.duration or 0,
            "art_url": self._get_art_url(track),
        }

    def _get_art_url(self, track):
        """Get album art URL via CloudFront for Alexa display."""
        thumb = track.thumb or track.parentThumb or track.grandparentThumb
        if thumb:
            return f"{self.stream_base_url}{thumb}?X-Plex-Token={self.token}"
        return None
