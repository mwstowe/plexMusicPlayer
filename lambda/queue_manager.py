"""Playback queue management for the Alexa AudioPlayer."""

import logging
import random

logger = logging.getLogger(__name__)


class PlaybackQueue:
    """Manages a queue of tracks for sequential/shuffle playback."""

    def __init__(self):
        self.tracks = []
        self.current_index = 0
        self.shuffle_enabled = False
        self.loop_enabled = False

    def load(self, tracks):
        """Load a list of tracks into the queue."""
        self.tracks = list(tracks)
        self.current_index = 0
        logger.info("Loaded %d tracks into queue", len(self.tracks))

    def shuffle(self):
        """Shuffle the remaining tracks (keeps current track in place)."""
        if len(self.tracks) <= 1:
            return
        current_track = self.tracks[self.current_index]
        remaining = self.tracks[self.current_index + 1:]
        random.shuffle(remaining)
        self.tracks = self.tracks[:self.current_index + 1] + remaining
        self.shuffle_enabled = True
        logger.info("Queue shuffled")

    def shuffle_all(self):
        """Shuffle all tracks including the first one."""
        if len(self.tracks) <= 1:
            return
        random.shuffle(self.tracks)
        self.current_index = 0
        self.shuffle_enabled = True
        logger.info("Full queue shuffled")

    def current_track(self):
        """Get the currently playing track."""
        if not self.tracks or self.current_index >= len(self.tracks):
            return None
        return self.tracks[self.current_index]

    def next_track(self):
        """Advance to the next track and return it."""
        if not self.tracks:
            return None

        self.current_index += 1
        if self.current_index >= len(self.tracks):
            if self.loop_enabled:
                self.current_index = 0
            else:
                return None

        return self.tracks[self.current_index]

    def previous_track(self):
        """Go back to the previous track and return it."""
        if not self.tracks:
            return None

        self.current_index = max(0, self.current_index - 1)
        return self.tracks[self.current_index]

    def has_next(self):
        """Check if there's a next track available."""
        if self.loop_enabled and self.tracks:
            return True
        return self.current_index < len(self.tracks) - 1

    def has_previous(self):
        """Check if there's a previous track available."""
        return self.current_index > 0

    def size(self):
        """Return the total number of tracks in the queue."""
        return len(self.tracks)

    def remaining(self):
        """Return the number of tracks remaining (including current)."""
        return max(0, len(self.tracks) - self.current_index)

    def to_dict(self):
        """Serialize queue state for session persistence."""
        return {
            "current_index": self.current_index,
            "shuffle_enabled": self.shuffle_enabled,
            "loop_enabled": self.loop_enabled,
            "track_count": len(self.tracks),
        }
