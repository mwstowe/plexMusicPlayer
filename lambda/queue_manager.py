"""Playback queue management for the Alexa AudioPlayer."""

import logging
import random

logger = logging.getLogger(__name__)


class PlaybackQueue:
    """Manages a queue of tracks for sequential/shuffle playback.

    Supports two modes:
    - Full: all tracks are loaded in memory (after a fresh Play command)
    - Partial: only a window of tracks is loaded, with all_keys holding
      the full list of rating keys (after a cold-start DynamoDB restore)

    Attributes:
        tracks: List of fetched track objects currently in memory.
        current_index: Index into `tracks` for the currently playing track.
        all_keys: Full list of rating keys (strings). When set, represents the
            complete queue even if only a subset of tracks are fetched.
        base_index: The offset into all_keys that corresponds to tracks[0].
            e.g., if base_index=50, then tracks[0] is all_keys[50].
        shuffle_enabled: Whether shuffle mode is active.
        loop_enabled: Whether loop mode is active.
        pause_offset_ms: Playback offset when paused (for resume).
    """

    def __init__(self):
        self.tracks = []
        self.current_index = 0
        self.all_keys = None
        self.base_index = 0
        self.shuffle_enabled = False
        self.loop_enabled = False
        self.pause_offset_ms = 0

    def load(self, tracks):
        """Load a list of tracks into the queue (fresh play command)."""
        self.tracks = list(tracks)
        self.current_index = 0
        self.all_keys = None
        self.base_index = 0
        self.pause_offset_ms = 0
        logger.info("Loaded %d tracks into queue", len(self.tracks))

    def load_from_keys(self, all_keys, current_index, tracks, shuffle_enabled=False, loop_enabled=False):
        """Load queue from a DynamoDB restore (partial track list).

        Args:
            all_keys: Full list of rating key strings.
            current_index: The absolute index into all_keys.
            tracks: Fetched track objects starting at current_index.
            shuffle_enabled: Persisted shuffle state.
            loop_enabled: Persisted loop state.
        """
        self.all_keys = all_keys
        self.base_index = current_index
        self.tracks = list(tracks)
        self.current_index = 0
        self.shuffle_enabled = shuffle_enabled
        self.loop_enabled = loop_enabled
        self.pause_offset_ms = 0
        logger.info("Loaded from keys: %d total, %d fetched, base=%d",
                    len(all_keys), len(tracks), current_index)

    @property
    def total_tracks(self):
        """Total number of tracks in the queue (all_keys or tracks)."""
        if self.all_keys:
            return len(self.all_keys)
        return len(self.tracks)

    @property
    def absolute_index(self):
        """The absolute index into the full queue (accounting for base_index)."""
        return self.base_index + self.current_index

    def get_all_keys(self):
        """Get the full list of rating keys for persistence.

        If all_keys is set (restored queue), returns that.
        Otherwise, builds it from the loaded tracks.
        """
        if self.all_keys:
            return self.all_keys
        return [str(t.ratingKey) for t in self.tracks]

    def shuffle(self):
        """Shuffle the remaining tracks (keeps current track in place).

        If operating on a partially-loaded queue (all_keys is set),
        shuffles the remaining keys rather than just the loaded tracks.
        The loaded tracks beyond current are discarded since their order
        is now invalid — they'll be re-fetched on demand.
        """
        if self.all_keys:
            # Shuffle the keys from current position + 1 onwards
            current_abs = self.base_index + self.current_index
            remaining_keys = self.all_keys[current_abs + 1:]
            random.shuffle(remaining_keys)
            self.all_keys = self.all_keys[:current_abs + 1] + remaining_keys

            # Discard pre-fetched tracks beyond current (order is now wrong)
            self.tracks = self.tracks[:self.current_index + 1]
            self.shuffle_enabled = True
            logger.info("Queue shuffled (all_keys): %d remaining keys shuffled", len(remaining_keys))
        else:
            # Full queue in memory — shuffle loaded tracks
            if len(self.tracks) <= 1:
                return
            remaining = self.tracks[self.current_index + 1:]
            random.shuffle(remaining)
            self.tracks = self.tracks[:self.current_index + 1] + remaining
            self.shuffle_enabled = True
            logger.info("Queue shuffled: %d remaining tracks", len(remaining))

    def shuffle_all(self):
        """Shuffle all tracks including the first one."""
        if len(self.tracks) <= 1:
            return
        random.shuffle(self.tracks)
        self.current_index = 0
        self.base_index = 0
        self.shuffle_enabled = True
        # Reset all_keys to match shuffled order
        self.all_keys = None
        logger.info("Full queue shuffled")

    def current_track(self):
        """Get the currently playing track."""
        if not self.tracks or self.current_index >= len(self.tracks):
            return None
        return self.tracks[self.current_index]

    def next_track(self):
        """Advance to the next track and return it.

        Returns the track, or None if at the end (and not looping).
        Does NOT fetch tracks — caller must handle fetch-on-demand
        when this returns None but has_next_key() is True.
        """
        if not self.tracks:
            return None

        self.current_index += 1
        self.pause_offset_ms = 0
        if self.current_index >= len(self.tracks):
            if self.loop_enabled and not self.all_keys:
                # Full queue in memory — loop to start
                self.current_index = 0
                self.base_index = 0
            else:
                # Partial queue or no loop — revert and let caller handle
                # (caller should check has_next_key() and fetch, or loop externally)
                self.current_index -= 1
                return None

        return self.tracks[self.current_index]

    def has_next_key(self):
        """Check if there's a next track key available beyond loaded tracks."""
        if not self.all_keys:
            return False
        next_absolute = self.base_index + self.current_index + 1
        return next_absolute < len(self.all_keys)

    def next_key(self):
        """Get the next rating key to fetch (for on-demand loading)."""
        if not self.all_keys:
            return None
        next_absolute = self.base_index + self.current_index + 1
        if next_absolute < len(self.all_keys):
            return self.all_keys[next_absolute]
        if self.loop_enabled and self.all_keys:
            return self.all_keys[0]
        return None

    def append_and_advance(self, track):
        """Append a newly fetched track and advance to it."""
        self.trim_before_current()
        self.tracks.append(track)
        self.current_index = len(self.tracks) - 1
        self.pause_offset_ms = 0

    def trim_before_current(self):
        """Trim tracks before current_index to prevent unbounded list growth.

        Adjusts base_index so absolute_index stays correct.
        Keeps the current track and one before it (for previous_track).
        """
        if self.current_index <= 1:
            return
        trim_count = self.current_index - 1
        self.tracks = self.tracks[trim_count:]
        self.base_index += trim_count
        self.current_index = 1
        logger.info("Trimmed %d tracks from queue head, base_index now %d",
                    trim_count, self.base_index)

    def previous_track(self):
        """Go back to the previous track and return it."""
        if not self.tracks:
            return None

        self.current_index = max(0, self.current_index - 1)
        self.pause_offset_ms = 0
        return self.tracks[self.current_index]

    def has_next(self):
        """Check if there's a next track available (loaded or fetchable)."""
        if self.loop_enabled and self.tracks:
            return True
        if self.current_index < len(self.tracks) - 1:
            return True
        return self.has_next_key()

    def has_previous(self):
        """Check if there's a previous track available."""
        return self.current_index > 0

    def size(self):
        """Return the total number of tracks in the queue."""
        return self.total_tracks

    def remaining(self):
        """Return the number of tracks remaining (including current)."""
        return max(0, self.total_tracks - self.absolute_index)

    def find_track_index(self, token):
        """Find the index of a track by its ratingKey token.

        Returns the index in self.tracks, or -1 if not found.
        """
        for i, track in enumerate(self.tracks):
            if str(track.ratingKey) == token:
                return i
        return -1
