# Plex Music Player

An Alexa skill that streams music from your Plex Media Server. Play songs, artists, albums, and playlists using voice commands on any Alexa-enabled device.

## Features

- **Play by song, artist, album, or playlist** — natural language search against your Plex library
- **Full playback controls** — next, previous, pause, resume, shuffle, loop, start over
- **Now Playing** — ask what's currently playing to get track/artist/album info
- **Queue management** — automatically queues multiple tracks when playing an artist or album
- **Album art cards** — shows track info and artwork in the Alexa app
- **Transcoding support** — automatically handles non-MP3 files via Plex's built-in transcoder

## Architecture

```
┌──────────┐     ┌─────────────────┐     ┌──────────────────┐
│  Alexa   │────▶│  AWS Lambda     │────▶│  Plex Media      │
│  Device  │◀────│  (ask-sdk +     │◀────│  Server          │
│          │     │   plexapi)      │     │                  │
└──────────┘     └─────────────────┘     └──────────────────┘
     │                                            │
     └────────── Audio Stream (HTTPS) ────────────┘
```

- **Alexa** sends voice intents to the Lambda function
- **Lambda** searches Plex and returns AudioPlayer directives with stream URLs
- **Alexa** streams audio directly from your Plex server (not through Lambda)

## Prerequisites

- An AWS account
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) installed
- [ASK CLI](https://developer.amazon.com/docs/smapi/quick-start-alexa-skills-kit-command-line-interface.html) (optional, for skill management)
- A Plex Media Server with a music library
- Your Plex server must be accessible via HTTPS from the internet (Alexa requires HTTPS for audio streaming)
- An [Amazon Developer account](https://developer.amazon.com/)

## Setup

### 1. Get Your Plex Token

1. Open Plex Web App and sign in
2. Navigate to any media item and click "Get Info" → "View XML"
3. In the URL bar, find `X-Plex-Token=YOUR_TOKEN`
4. Alternatively, check `~/.config/Plex Media Server/Preferences.xml` on your server

### 2. Ensure HTTPS Access to Plex

Alexa will only stream audio from HTTPS URLs. Options:

- **Plex's built-in remote access** — if enabled, Plex provides an HTTPS URL like `https://xxx.plex.direct:32400`
- **Reverse proxy** — put nginx/Caddy in front of Plex with a valid TLS certificate
- **Plex Relay** — works but has bandwidth limits on free accounts

To find your external HTTPS URL, go to Plex Settings → Remote Access and note the public URL.

### 3. Create the Alexa Skill

1. Go to the [Alexa Developer Console](https://developer.amazon.com/alexa/console/ask)
2. Click "Create Skill"
3. Name: `Plex Music Player`
4. Model: Custom
5. Backend: Provision your own (Lambda)
6. Language: English (US)
7. After creation, go to **JSON Editor** under "Interaction Model" and paste the contents of `skill-package/interactionModels/custom/en-US.json`
8. Under **Interfaces**, enable **Audio Player**
9. Build the model
10. Note your **Skill ID** (shown at top of the skill page: `amzn1.ask.skill.xxx`)

### 4. Deploy the Lambda Function

```bash
# Set required environment variables
export PLEX_URL="https://your-plex-server.plex.direct:32400"
export PLEX_TOKEN="your_plex_token"
export PLEX_MUSIC_LIBRARY="Music"  # name of your music library section
export ALEXA_SKILL_ID="amzn1.ask.skill.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# Deploy using the included script
./deploy.sh
```

Or manually with SAM:

```bash
sam build --template-file template.yaml
sam deploy --guided
```

### 5. Connect the Skill to Lambda

1. After deployment, get the Lambda ARN from the output:
   ```bash
   aws cloudformation describe-stacks \
     --stack-name plex-music-player \
     --query 'Stacks[0].Outputs[?OutputKey==`FunctionArn`].OutputValue' \
     --output text
   ```
2. In the Alexa Developer Console, go to your skill → **Endpoint**
3. Select **AWS Lambda ARN**
4. Paste the ARN in the **Default Region** field
5. Save

### 6. Test

In the Alexa Developer Console, go to the **Test** tab:

- Say: "Alexa, open plex music"
- Say: "Alexa, ask plex music to play Arctic Monkeys"
- Say: "Alexa, ask plex music to play the album AM"
- Say: "Alexa, ask plex music to play my favorites playlist"

## Voice Commands

| Command | Example |
|---------|---------|
| Play a song | "Alexa, ask plex music to play Bohemian Rhapsody" |
| Play an artist | "Alexa, ask plex music to play songs by Queen" |
| Play an album | "Alexa, ask plex music to play the album A Night at the Opera" |
| Play a playlist | "Alexa, ask plex music to play my workout playlist" |
| What's playing | "Alexa, ask plex music what's playing" |
| Next track | "Alexa, next" |
| Previous track | "Alexa, previous" |
| Pause | "Alexa, pause" |
| Resume | "Alexa, resume" |
| Shuffle | "Alexa, shuffle" |
| Loop | "Alexa, loop" |
| Start over | "Alexa, start over" |

## Project Structure

```
plexMusicPlayer/
├── lambda/
│   ├── lambda_function.py   # Main Alexa skill handler (ask-sdk)
│   ├── plex_client.py       # Plex Media Server integration (plexapi)
│   └── queue_manager.py     # Playback queue management
├── skill-package/
│   ├── interactionModels/
│   │   └── custom/
│   │       └── en-US.json   # Alexa interaction model
│   └── skill.json           # Skill manifest
├── template.yaml            # AWS SAM template
├── deploy.sh                # Deployment script
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
└── README.md
```

## Configuration

| Environment Variable | Required | Description |
|---------------------|----------|-------------|
| `PLEX_URL` | Yes | Your Plex server's HTTPS URL (e.g., `https://xxx.plex.direct:32400`) |
| `PLEX_TOKEN` | Yes | Your Plex authentication token |
| `PLEX_MUSIC_LIBRARY` | No | Name of your music library section (default: `Music`) |

## Troubleshooting

### "I couldn't find that song/artist/album"
- Verify the track exists in your Plex music library
- Check that `PLEX_MUSIC_LIBRARY` matches your library name exactly (case-sensitive)
- Check Lambda CloudWatch logs for search details

### Audio won't play / playback fails
- Ensure your Plex server is accessible via HTTPS from the internet
- Test the stream URL in a browser to verify it works
- Check that Plex remote access is enabled
- Alexa supports MP3, AAC, and HLS — if your files are FLAC, the transcoding URL should handle conversion automatically

### Lambda timeout errors
- The default timeout is 10 seconds; increase if your Plex server is slow to respond
- Consider Lambda's cold start time on first invocation

### "There was an error connecting to your Plex server"
- Verify `PLEX_URL` and `PLEX_TOKEN` are correct
- Ensure the Lambda function has internet access (it does by default)
- Check that your Plex server isn't blocking the connection

## Development

### Local Testing

```bash
# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set environment variables
export PLEX_URL="https://your-server:32400"
export PLEX_TOKEN="your_token"

# Test Plex connectivity
python3 -c "
from lambda.plex_client import PlexMusicClient
client = PlexMusicClient()
print(f'Connected to: {client.server.friendlyName}')
print(f'Music library: {client.music_library.title}')
print(f'Tracks: {client.music_library.totalSize}')
"
```

### SAM Local Invoke

```bash
sam build
sam local invoke PlexMusicPlayerFunction --event events/play_artist.json
```

## Limitations

- **No multi-user support** — uses a single Plex token (designed for personal use)
- **No persistent queue** — the playback queue resets if Lambda cold-starts mid-playback (for production use, add DynamoDB persistence)
- **Single music library** — searches one library section at a time

## Credits

Inspired by [Tyzer34/plexMusicPlayer](https://github.com/Tyzer34/plexMusicPlayer), rebuilt from scratch with modern tooling:
- [ask-sdk](https://github.com/alexa/alexa-skills-kit-sdk-for-python) instead of deprecated Flask-Ask
- [plexapi](https://github.com/pkkid/python-plexapi) for robust Plex integration
- AWS Lambda + SAM instead of Heroku

## License

GPL-3.0
