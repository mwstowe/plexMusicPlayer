"""Plex Media Server integration for music playback."""

import logging
import os
from urllib.parse import urlencode
from xml.etree import ElementTree

import requests
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

logger = logging.getLogger(__name__)

PLEX_TV_RESOURCES_URL = "https://plex.tv/api/resources"


def resolve_plex_url(token, timeout=5):
    """Resolve the Plex server URL dynamically via plex.tv API.

    Queries plex.tv/api/resources?includeHttps=1 to find the server's
    current connection URI. Prefers the remote (local="0") HTTPS
    .plex.direct connection, which tracks the server's current public IP.

    Returns the best available URL, or None if resolution fails.
    """
    try:
        resp = requests.get(
            PLEX_TV_RESOURCES_URL,
            params={"X-Plex-Token": token, "includeHttps": "1"},
            headers={"Accept": "application/xml"},
            timeout=timeout,
        )
        resp.raise_for_status()

        root = ElementTree.fromstring(resp.text)

        # Find server devices (product="Plex Media Server")
        for device in root.findall("./Device[@product='Plex Media Server']"):
            connections = device.findall(".//Connection")

            # First pass: remote HTTPS .plex.direct connection (ideal for Lambda)
            for conn in connections:
                uri = conn.get("uri", "")
                if (conn.get("protocol") == "https"
                        and conn.get("local") == "0"
                        and "plex.direct" in uri):
                    logger.info("Resolved Plex URL via plex.tv: %s", uri)
                    return uri

            # Second pass: any remote HTTPS connection
            for conn in connections:
                uri = conn.get("uri", "")
                if (conn.get("protocol") == "https"
                        and conn.get("local") == "0"):
                    logger.info("Resolved Plex URL via plex.tv (non-direct): %s", uri)
                    return uri

        logger.warning("No suitable Plex server connection found in plex.tv response")
        return None

    except Exception as e:
        logger.warning("Failed to resolve Plex URL from plex.tv: %s", e)
        return None


class PlexMusicClient:
    """Client for interacting with Plex music libraries."""

    def __init__(self, base_url=None, token=None, library_name=None):
        self.token = token or os.environ["PLEX_TOKEN"]
        self.base_url = base_url or self._resolve_base_url()
        self.library_name = library_name or os.environ.get("PLEX_MUSIC_LIBRARY", "Music")
        self.stream_base_url = os.environ.get("STREAM_BASE_URL", self.base_url)
        self._server = None
        self._music_library = None

    def _resolve_base_url(self):
        """Resolve the Plex server URL, trying plex.tv first, then env var fallback."""
        resolved = resolve_plex_url(self.token)
        if resolved:
            return resolved

        plex_url = os.environ.get("PLEX_URL")
        if plex_url:
            logger.info("Using PLEX_URL env var as fallback: %s", plex_url)
            return plex_url

        raise ValueError(
            "Cannot determine Plex server URL: plex.tv resolution failed "
            "and PLEX_URL environment variable is not set"
        )

    @property
    def server(self):
        if self._server is None:
            self._server = PlexServer(self.base_url, self.token, timeout=8)
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
        """Fetch tracks by their rating keys (for queue restoration).

        Uses Plex's batch metadata endpoint to fetch multiple tracks
        in a single HTTP request: /library/metadata/key1,key2,key3
        """
        if not rating_keys:
            return []

        try:
            key_path = ",".join(str(k) for k in rating_keys)
            results = self.server.fetchItems(f"/library/metadata/{key_path}")
            logger.info("get_tracks_by_keys: requested %d, got %d", len(rating_keys), len(results))
            return results
        except Exception as e:
            logger.error("Batch fetch failed for %d keys: %s", len(rating_keys), e)
            # Fall back to one-at-a-time fetch
            tracks = []
            for key in rating_keys:
                try:
                    results = self.server.fetchItems(f"/library/metadata/{key}")
                    if results:
                        tracks.append(results[0])
                except Exception as inner_e:
                    logger.error("Failed to fetch track %s: %s", key, inner_e)
                    continue
            logger.info("get_tracks_by_keys (fallback): requested %d, got %d", len(rating_keys), len(tracks))
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

    def report_playback(self, track, state="playing", time_ms=0):
        """Report playback state to Plex timeline API.

        This makes Plex aware of what's playing (shows in Now Playing,
        Tautulli, etc.)

        Args:
            track: The track object being played
            state: One of 'playing', 'paused', 'stopped'
            time_ms: Current playback position in milliseconds
        """
        try:
            params = {
                "ratingKey": track.ratingKey,
                "key": track.key,
                "state": state,
                "time": time_ms,
                "duration": track.duration or 0,
                "X-Plex-Client-Identifier": "plexMusicPlayer-alexa",
                "X-Plex-Product": "Plex Music Player",
                "X-Plex-Device": "Alexa",
                "X-Plex-Token": self.token,
            }
            self.server.query(f"/:/timeline?{urlencode(params)}")
            logger.info("Reported timeline: %s state=%s", track.title, state)
        except Exception as e:
            logger.warning("Failed to report timeline: %s", e)
