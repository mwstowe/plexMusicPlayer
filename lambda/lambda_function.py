"""AWS Lambda handler for the plexMusicPlayer Alexa skill."""

import logging
import os

from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import (
    AbstractRequestHandler,
    AbstractExceptionHandler,
)
from ask_sdk_core.utils import is_intent_name, is_request_type
from ask_sdk_model.interfaces.audioplayer import (
    PlayDirective,
    PlayBehavior,
    AudioItem,
    Stream,
    StopDirective,
    AudioItemMetadata,
)
from ask_sdk_model.ui import StandardCard, Image

from plex_client import PlexMusicClient
from queue_manager import PlaybackQueue

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Global state (persists across warm Lambda invocations)
plex_client = PlexMusicClient()
queue = PlaybackQueue()

sb = SkillBuilder()


# --- Helper Functions ---


def build_audio_play_directive(track, plex, enqueue=False):
    """Build an AudioPlayer.Play directive for a given track."""
    stream_url = plex.get_stream_url(track)
    track_info = plex.get_track_info(track)

    play_behavior = PlayBehavior.ENQUEUE if enqueue else PlayBehavior.REPLACE_ALL

    metadata = AudioItemMetadata(
        title=track_info["title"],
        subtitle=f"{track_info['artist']} — {track_info['album']}",
    )

    directive = PlayDirective(
        play_behavior=play_behavior,
        audio_item=AudioItem(
            stream=Stream(
                token=str(track.ratingKey),
                url=stream_url,
                offset_in_milliseconds=0,
                expected_previous_token=None,
            ),
            metadata=metadata,
        ),
    )
    return directive, track_info


def build_card(track_info, plex):
    """Build a visual card for the Alexa app."""
    # Note: Alexa card images must be on port 443 and publicly accessible.
    # Plex art URLs on port 32400 will be rejected, so skip images for now.
    return StandardCard(
        title=track_info["title"],
        text=f"{track_info['artist']} — {track_info['album']}",
        image=None,
    )


def play_track(handler_input, track, speech_text=None):
    """Play a track and return the response."""
    directive, track_info = build_audio_play_directive(track, plex_client)

    stream_url = plex_client.get_stream_url(track)
    logger.info(
        "Playing track: %s | URL: %s | Token: %s",
        track_info["title"],
        stream_url,
        str(track.ratingKey),
    )

    response_builder = handler_input.response_builder
    response_builder.add_directive(directive)

    return response_builder.response


# --- Intent Handlers ---


class LaunchRequestHandler(AbstractRequestHandler):
    """Handle skill launch."""

    def can_handle(self, handler_input):
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        speech = (
            "Welcome to Plex Music Player. "
            "You can ask me to play a song, artist, album, or playlist from your Plex library. "
            "What would you like to hear?"
        )
        return (
            handler_input.response_builder.speak(speech)
            .ask("What would you like to listen to?")
            .response
        )


class PlaySongIntentHandler(AbstractRequestHandler):
    """Handle requests to play a specific song."""

    def can_handle(self, handler_input):
        return is_intent_name("PlaySongIntent")(handler_input)

    def handle(self, handler_input):
        slots = handler_input.request_envelope.request.intent.slots
        song_name = slots.get("song", {})
        song_query = song_name.value if song_name else None

        if not song_query:
            return (
                handler_input.response_builder.speak(
                    "I didn't catch the song name. What song would you like to hear?"
                )
                .ask("What song would you like me to play?")
                .response
            )

        tracks = plex_client.search_tracks(song_query)
        if not tracks:
            return (
                handler_input.response_builder.speak(
                    f"I couldn't find a song called {song_query} in your Plex library."
                )
                .ask("Would you like to try a different song?")
                .response
            )

        queue.load(tracks)
        track = queue.current_track()
        track_info = plex_client.get_track_info(track)
        speech = f"Playing {track_info['title']} by {track_info['artist']}."

        return play_track(handler_input, track, speech)


class PlayArtistIntentHandler(AbstractRequestHandler):
    """Handle requests to play music by a specific artist."""

    def can_handle(self, handler_input):
        return is_intent_name("PlayArtistIntent")(handler_input)

    def handle(self, handler_input):
        slots = handler_input.request_envelope.request.intent.slots
        artist_name = slots.get("artist", {})
        artist_query = artist_name.value if artist_name else None

        if not artist_query:
            return (
                handler_input.response_builder.speak(
                    "Which artist would you like to hear?"
                )
                .ask("Which artist should I play?")
                .response
            )

        tracks = plex_client.search_artist(artist_query)
        if not tracks:
            return (
                handler_input.response_builder.speak(
                    f"I couldn't find any music by {artist_query} in your Plex library."
                )
                .ask("Would you like to try a different artist?")
                .response
            )

        queue.load(tracks)
        track = queue.current_track()
        track_info = plex_client.get_track_info(track)
        speech = (
            f"Playing {len(tracks)} tracks by {artist_query}, "
            f"starting with {track_info['title']}."
        )

        return play_track(handler_input, track, speech)


class PlayAlbumIntentHandler(AbstractRequestHandler):
    """Handle requests to play a specific album."""

    def can_handle(self, handler_input):
        return is_intent_name("PlayAlbumIntent")(handler_input)

    def handle(self, handler_input):
        slots = handler_input.request_envelope.request.intent.slots
        album_name = slots.get("album", {})
        artist_name = slots.get("artist", {})
        album_query = album_name.value if album_name else None
        artist_query = artist_name.value if artist_name else None

        if not album_query:
            return (
                handler_input.response_builder.speak(
                    "Which album would you like to hear?"
                )
                .ask("Which album should I play?")
                .response
            )

        tracks = plex_client.search_album(album_query, artist_query)
        if not tracks:
            return (
                handler_input.response_builder.speak(
                    f"I couldn't find an album called {album_query} in your Plex library."
                )
                .ask("Would you like to try a different album?")
                .response
            )

        queue.load(tracks)
        track = queue.current_track()
        track_info = plex_client.get_track_info(track)
        speech = (
            f"Playing the album {album_query} with {len(tracks)} tracks, "
            f"starting with {track_info['title']}."
        )

        return play_track(handler_input, track, speech)


class PlayPlaylistIntentHandler(AbstractRequestHandler):
    """Handle requests to play a Plex playlist."""

    def can_handle(self, handler_input):
        return is_intent_name("PlayPlaylistIntent")(handler_input)

    def handle(self, handler_input):
        slots = handler_input.request_envelope.request.intent.slots
        playlist_name = slots.get("playlist", {})
        playlist_query = playlist_name.value if playlist_name else None

        if not playlist_query:
            return (
                handler_input.response_builder.speak(
                    "Which playlist would you like to hear?"
                )
                .ask("Which playlist should I play?")
                .response
            )

        tracks = plex_client.get_playlist(playlist_query)
        if not tracks:
            return (
                handler_input.response_builder.speak(
                    f"I couldn't find a playlist called {playlist_query}."
                )
                .ask("Would you like to try a different playlist?")
                .response
            )

        queue.load(tracks)
        track = queue.current_track()
        track_info = plex_client.get_track_info(track)
        speech = (
            f"Playing the {playlist_query} playlist with {len(tracks)} tracks, "
            f"starting with {track_info['title']}."
        )

        return play_track(handler_input, track, speech)


class NowPlayingIntentHandler(AbstractRequestHandler):
    """Tell the user what's currently playing."""

    def can_handle(self, handler_input):
        return is_intent_name("NowPlayingIntent")(handler_input)

    def handle(self, handler_input):
        track = queue.current_track()
        if not track:
            return (
                handler_input.response_builder.speak("Nothing is currently playing.")
                .set_should_end_session(True)
                .response
            )

        track_info = plex_client.get_track_info(track)
        speech = (
            f"Now playing {track_info['title']} by {track_info['artist']} "
            f"from the album {track_info['album']}."
        )
        return (
            handler_input.response_builder.speak(speech)
            .set_card(build_card(track_info, plex_client))
            .set_should_end_session(True)
            .response
        )


class ShuffleOnIntentHandler(AbstractRequestHandler):
    """Enable shuffle mode."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.ShuffleOnIntent")(handler_input)

    def handle(self, handler_input):
        queue.shuffle()
        return (
            handler_input.response_builder.speak("Shuffle is now on.")
            .set_should_end_session(True)
            .response
        )


class ShuffleOffIntentHandler(AbstractRequestHandler):
    """Disable shuffle mode."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.ShuffleOffIntent")(handler_input)

    def handle(self, handler_input):
        queue.shuffle_enabled = False
        return (
            handler_input.response_builder.speak("Shuffle is now off.")
            .set_should_end_session(True)
            .response
        )


class LoopOnIntentHandler(AbstractRequestHandler):
    """Enable loop mode."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.LoopOnIntent")(handler_input)

    def handle(self, handler_input):
        queue.loop_enabled = True
        return (
            handler_input.response_builder.speak("Loop mode is now on.")
            .set_should_end_session(True)
            .response
        )


class LoopOffIntentHandler(AbstractRequestHandler):
    """Disable loop mode."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.LoopOffIntent")(handler_input)

    def handle(self, handler_input):
        queue.loop_enabled = False
        return (
            handler_input.response_builder.speak("Loop mode is now off.")
            .set_should_end_session(True)
            .response
        )


# --- AudioPlayer Event Handlers ---


class PlaybackNearlyFinishedHandler(AbstractRequestHandler):
    """Enqueue the next track when the current one is almost done."""

    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackNearlyFinished")(handler_input)

    def handle(self, handler_input):
        next_track = queue.next_track()
        if not next_track:
            return handler_input.response_builder.response

        directive, _ = build_audio_play_directive(next_track, plex_client, enqueue=True)
        return handler_input.response_builder.add_directive(directive).response


class PlaybackStartedHandler(AbstractRequestHandler):
    """Handle playback started event."""

    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackStarted")(handler_input)

    def handle(self, handler_input):
        return handler_input.response_builder.response


class PlaybackStoppedHandler(AbstractRequestHandler):
    """Handle playback stopped event."""

    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackStopped")(handler_input)

    def handle(self, handler_input):
        return handler_input.response_builder.response


class PlaybackFinishedHandler(AbstractRequestHandler):
    """Handle playback finished event."""

    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackFinished")(handler_input)

    def handle(self, handler_input):
        return handler_input.response_builder.response


class PlaybackFailedHandler(AbstractRequestHandler):
    """Handle playback failure."""

    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackFailed")(handler_input)

    def handle(self, handler_input):
        logger.error(
            "Playback failed: %s",
            handler_input.request_envelope.request,
        )
        return handler_input.response_builder.response


# --- Built-in Playback Control Intents ---


class PauseIntentHandler(AbstractRequestHandler):
    """Handle pause requests."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.PauseIntent")(handler_input)

    def handle(self, handler_input):
        return (
            handler_input.response_builder.add_directive(StopDirective())
            .set_should_end_session(True)
            .response
        )


class ResumeIntentHandler(AbstractRequestHandler):
    """Handle resume requests."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.ResumeIntent")(handler_input)

    def handle(self, handler_input):
        track = queue.current_track()
        if not track:
            return (
                handler_input.response_builder.speak(
                    "There's nothing to resume. Tell me what you'd like to play."
                )
                .ask("What would you like to listen to?")
                .response
            )
        return play_track(handler_input, track)


class NextIntentHandler(AbstractRequestHandler):
    """Handle next track requests."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.NextIntent")(handler_input)

    def handle(self, handler_input):
        track = queue.next_track()
        if not track:
            return (
                handler_input.response_builder.speak(
                    "You've reached the end of the queue."
                )
                .add_directive(StopDirective())
                .set_should_end_session(True)
                .response
            )

        track_info = plex_client.get_track_info(track)
        speech = f"Playing {track_info['title']} by {track_info['artist']}."
        return play_track(handler_input, track, speech)


class PreviousIntentHandler(AbstractRequestHandler):
    """Handle previous track requests."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.PreviousIntent")(handler_input)

    def handle(self, handler_input):
        track = queue.previous_track()
        if not track:
            return (
                handler_input.response_builder.speak(
                    "You're at the beginning of the queue."
                )
                .set_should_end_session(True)
                .response
            )

        track_info = plex_client.get_track_info(track)
        speech = f"Playing {track_info['title']} by {track_info['artist']}."
        return play_track(handler_input, track, speech)


class StartOverIntentHandler(AbstractRequestHandler):
    """Restart the current queue from the beginning."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.StartOverIntent")(handler_input)

    def handle(self, handler_input):
        if not queue.tracks:
            return (
                handler_input.response_builder.speak(
                    "There's nothing to restart. Tell me what you'd like to play."
                )
                .ask("What would you like to listen to?")
                .response
            )

        queue.current_index = 0
        track = queue.current_track()
        track_info = plex_client.get_track_info(track)
        speech = f"Starting over. Playing {track_info['title']} by {track_info['artist']}."
        return play_track(handler_input, track, speech)


class CancelStopIntentHandler(AbstractRequestHandler):
    """Handle cancel and stop."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.CancelIntent")(handler_input) or is_intent_name(
            "AMAZON.StopIntent"
        )(handler_input)

    def handle(self, handler_input):
        return (
            handler_input.response_builder.speak("Goodbye!")
            .add_directive(StopDirective())
            .set_should_end_session(True)
            .response
        )


class HelpIntentHandler(AbstractRequestHandler):
    """Handle help requests."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        speech = (
            "You can ask me to play a song, artist, album, or playlist from your Plex library. "
            "For example, say 'play songs by Arctic Monkeys', or 'play the album AM', "
            "or 'play my favorites playlist'. "
            "You can also say next, previous, shuffle, or ask what's playing."
        )
        return (
            handler_input.response_builder.speak(speech)
            .ask("What would you like to listen to?")
            .response
        )


class SessionEndedRequestHandler(AbstractRequestHandler):
    """Handle session ended."""

    def can_handle(self, handler_input):
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        return handler_input.response_builder.response


class FallbackIntentHandler(AbstractRequestHandler):
    """Handle unrecognized requests."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        speech = (
            "I'm not sure how to help with that. "
            "You can ask me to play a song, artist, album, or playlist."
        )
        return (
            handler_input.response_builder.speak(speech)
            .ask("What would you like to listen to?")
            .response
        )


# --- Exception Handler ---


class GlobalExceptionHandler(AbstractExceptionHandler):
    """Catch-all exception handler."""

    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error("Unhandled exception: %s", exception, exc_info=True)
        speech = "Sorry, something went wrong connecting to your Plex server. Please try again."
        return (
            handler_input.response_builder.speak(speech)
            .ask("Would you like to try again?")
            .response
        )


# --- Register Handlers ---

sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PlaySongIntentHandler())
sb.add_request_handler(PlayArtistIntentHandler())
sb.add_request_handler(PlayAlbumIntentHandler())
sb.add_request_handler(PlayPlaylistIntentHandler())
sb.add_request_handler(NowPlayingIntentHandler())
sb.add_request_handler(ShuffleOnIntentHandler())
sb.add_request_handler(ShuffleOffIntentHandler())
sb.add_request_handler(LoopOnIntentHandler())
sb.add_request_handler(LoopOffIntentHandler())
sb.add_request_handler(PauseIntentHandler())
sb.add_request_handler(ResumeIntentHandler())
sb.add_request_handler(NextIntentHandler())
sb.add_request_handler(PreviousIntentHandler())
sb.add_request_handler(StartOverIntentHandler())
sb.add_request_handler(CancelStopIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(PlaybackNearlyFinishedHandler())
sb.add_request_handler(PlaybackStartedHandler())
sb.add_request_handler(PlaybackStoppedHandler())
sb.add_request_handler(PlaybackFinishedHandler())
sb.add_request_handler(PlaybackFailedHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_exception_handler(GlobalExceptionHandler())

# Lambda handler entry point
handler = sb.lambda_handler()
