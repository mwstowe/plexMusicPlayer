"""Plex Media Server integration for music playback."""

import difflib
import logging
import os
from urllib.parse import urlencode
from xml.etree import ElementTree

import requests
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

logger = logging.getLogger(__name__)

PLEX_TV_RESOURCES_URL = "https://plex.tv/api/resources"

# Manual overrides for artist names that Alexa frequently misinterprets.
# Keys should be lowercase. Values are the correct artist name in your library.
# Examples:
#   "led zeppelin": "Led Zeppelin",
#   "ac dc": "AC/DC",
#   "guns and roses": "Guns N' Roses",
ARTIST_MAPPINGS = {}


def resolve_plex_urls(token, timeout=5):
    """Resolve Plex server URLs dynamically via plex.tv API.

    Queries plex.tv/api/resources?includeHttps=1 to find the server's
    current connection URIs. Returns both the direct URL (for Lambda API
    calls) and the relay URL (for Alexa streaming fallback) in a single
    network request.

    Returns:
        dict with keys:
            "direct": The remote HTTPS .plex.direct URL (or best alternative), or None
            "relay": The Plex relay URL (HTTPS port 443, trusted cert), or None
    """
    result = {"direct": None, "relay": None}

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

            # Look for direct URL: prefer remote HTTPS .plex.direct
            for conn in connections:
                uri = conn.get("uri", "")
                if (conn.get("protocol") == "https"
                        and conn.get("local") == "0"
                        and "plex.direct" in uri):
                    result["direct"] = uri
                    break

            # Fallback direct: any remote HTTPS connection
            if not result["direct"]:
                for conn in connections:
                    uri = conn.get("uri", "")
                    if (conn.get("protocol") == "https"
                            and conn.get("local") == "0"):
                        result["direct"] = uri
                        break

            # Look for relay URL
            for conn in connections:
                if conn.get("relay") == "1":
                    result["relay"] = conn.get("uri", "")
                    break

            # Use first PMS device found (most users have one server)
            if result["direct"]:
                break

        if result["direct"]:
            logger.info("Resolved Plex direct URL via plex.tv: %s", result["direct"])
        if result["relay"]:
            logger.info("Resolved Plex relay URL via plex.tv: %s", result["relay"])
        if not result["direct"] and not result["relay"]:
            logger.warning("No suitable Plex server connection found in plex.tv response")

        return result

    except Exception as e:
        logger.warning("Failed to resolve Plex URLs from plex.tv: %s", e)
        return result


class PlexMusicClient:
    """Client for interacting with Plex music libraries.

    Note: When STREAM_BASE_URL is not set, the client will attempt to detect
    a Plex relay URL as a fallback for streaming. Relay URLs provide HTTPS on
    port 443 with trusted certificates (compatible with Alexa), but have
    bandwidth limitations imposed by Plex. For best audio quality and
    reliability, set STREAM_BASE_URL to a CloudFront distribution that
    proxies your Plex server.
    """

    def __init__(self, base_url=None, token=None, library_name=None):
        self.token = token or os.environ["PLEX_TOKEN"]
        self.library_name = library_name or os.environ.get("PLEX_MUSIC_LIBRARY", "Music")
        self._server = None
        self._music_library = None
        self._artist_cache = None

        # Resolve server URLs: direct (for API calls) and relay (for streaming fallback)
        if base_url:
            self.base_url = base_url
            self._relay_url = None
        else:
            self.base_url, self._relay_url = self._resolve_base_url()

        # Determine streaming URL priority:
        # 1. Explicit STREAM_BASE_URL (CloudFront) — best quality, no bandwidth caps
        # 2. Plex relay URL — HTTPS port 443, trusted cert, but bandwidth-limited
        # 3. base_url — may not work with Alexa (wrong port / untrusted cert)
        explicit_stream_url = os.environ.get("STREAM_BASE_URL")
        if explicit_stream_url:
            self.stream_base_url = explicit_stream_url
        elif self._relay_url:
            logger.info("Using Plex relay URL for streaming: %s", self._relay_url)
            self.stream_base_url = self._relay_url
        else:
            logger.warning(
                "No STREAM_BASE_URL configured and no relay URL available. "
                "Streaming will use base_url (%s) which may not work with "
                "Alexa (requires HTTPS on port 443 with trusted cert).",
                self.base_url,
            )
            self.stream_base_url = self.base_url

    def _resolve_base_url(self):
        """Resolve Plex server URLs with a single plex.tv call.

        Returns:
            Tuple of (base_url, relay_url). relay_url may be None.

        Raises:
            ValueError: If no server URL can be determined.
        """
        urls = resolve_plex_urls(self.token)

        base_url = urls["direct"]
        relay_url = urls["relay"]

        if base_url:
            return base_url, relay_url

        # plex.tv resolution failed — try env var fallback
        plex_url = os.environ.get("PLEX_URL")
        if plex_url:
            logger.info("Using PLEX_URL env var as fallback: %s", plex_url)
            return plex_url, relay_url

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

    def _get_artist_cache(self):
        """Lazily load and return a list of all artist names in the library."""
        if self._artist_cache is None:
            artists = self.music_library.searchArtists()
            self._artist_cache = [artist.title for artist in artists]
            logger.info("Loaded artist cache: %d artists", len(self._artist_cache))
        return self._artist_cache

    def _fuzzy_match_artist(self, spoken_name):
        """Attempt to match a spoken artist name to a library artist.

        Checks manual ARTIST_MAPPINGS first (case-insensitive), then falls
        back to difflib fuzzy matching against the full artist cache.

        Returns the matched name, or the original spoken_name if no match found.
        """
        # Check manual overrides first (case-insensitive)
        mapping_key = spoken_name.lower()
        for key, value in ARTIST_MAPPINGS.items():
            if key.lower() == mapping_key:
                logger.info("Artist mapping override: '%s' -> '%s'", spoken_name, value)
                return value

        # Fuzzy match against cached artist names
        cache = self._get_artist_cache()
        matches = difflib.get_close_matches(spoken_name, cache, n=1, cutoff=0.6)
        if matches:
            logger.info("Fuzzy matched artist: '%s' -> '%s'", spoken_name, matches[0])
            return matches[0]

        return spoken_name

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

        Falls back to fuzzy matching when exact search fails.
        """
        # First try library-level artist search
        results = self.music_library.searchArtists(title=artist_name)
        if results:
            artist = results[0]
            tracks = artist.tracks()
            if tracks:
                return tracks

        # Try fuzzy matching before falling back to track-level search
        fuzzy_name = self._fuzzy_match_artist(artist_name)
        if fuzzy_name != artist_name:
            results = self.music_library.searchArtists(title=fuzzy_name)
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

        Uses stream_base_url which may be:
        - CloudFront (STREAM_BASE_URL env var) — CDN in front of Plex, best quality
        - Plex relay — auto-detected HTTPS proxy with bandwidth limits
        - Direct Plex URL — fallback, may not work with Alexa

        The Plex token is passed as a query parameter for authentication.
        """
        if track.media and track.media[0].parts:
            part = track.media[0].parts[0]
            stream_url = f"{self.stream_base_url}{part.key}?X-Plex-Token={self.token}"
            return stream_url

        # Fallback (shouldn't happen for music tracks)
        return f"{self.stream_base_url}{track.key}?X-Plex-Token={self.token}"

    def get_track_info(self, track):
        """Extract metadata from a track for Alexa cards/speech.

        Uses safe attribute access to avoid plexapi's lazy-reload behavior.
        When plexapi encounters a None attribute, it re-fetches the item from
        the server — which can timeout if Plex is momentarily unreachable.
        We use getattr with _data fallback to avoid triggering reloads.
        """
        try:
            # Prefer originalTitle (per-track artist from ID3 tags) over
            # grandparentTitle (library folder artist, often "Various Artists")
            artist = (self._safe_attr(track, "originalTitle")
                      or self._safe_attr(track, "grandparentTitle")
                      or "Unknown Artist")
            return {
                "title": self._safe_attr(track, "title") or "Unknown Track",
                "artist": artist,
                "album": self._safe_attr(track, "parentTitle") or "Unknown Album",
                "duration_ms": self._safe_attr(track, "duration") or 0,
                "art_url": self._get_art_url(track),
            }
        except Exception as e:
            logger.warning("Failed to get track info: %s", e)
            # Return minimal info so playback can still proceed
            return {
                "title": getattr(track, "_title", None) or "Unknown Track",
                "artist": "Unknown Artist",
                "album": "Unknown Album",
                "duration_ms": 0,
                "art_url": None,
            }

    @staticmethod
    def _safe_attr(track, attr_name):
        """Access a track attribute without triggering plexapi's lazy reload.

        plexapi's __getattribute__ triggers a full server reload when an
        attribute is None (to handle partial fetches). This bypasses that
        by reading directly from the object's __dict__ or _data XML element.
        """
        # First try __dict__ (already-set Python attributes)
        val = track.__dict__.get(attr_name)
        if val is not None:
            return val
        # Fall back to the raw XML data if available (avoids network call)
        if hasattr(track, "_data") and track._data is not None:
            val = track._data.attrib.get(attr_name)
            if val is not None:
                return val
        # Last resort: try normal access but with a short timeout guard
        # (this may trigger a reload, but we catch failures in the caller)
        return None

    def _get_art_url(self, track):
        """Get album art URL via CloudFront for Alexa display."""
        thumb = (self._safe_attr(track, "thumb")
                 or self._safe_attr(track, "parentThumb")
                 or self._safe_attr(track, "grandparentThumb"))
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
