# Plex Music Player

An Alexa skill that streams music from your Plex Media Server. Play songs, artists, albums, and playlists using voice commands on any Alexa-enabled device.

## Features

- **Play by song, artist, album, or playlist** — natural language search against your Plex library
- **Full playback controls** — next, previous, pause, resume, shuffle, loop, start over
- **Now Playing** — ask what's currently playing to get track/artist/album info with display support for Echo Show
- **Queue management** — automatically queues multiple tracks when playing an artist or album
- **Compilation support** — correctly identifies per-track artists in "Various Artists" folders

## Architecture

```
                          ┌─────────────────┐
┌──────────┐  Intents    │  AWS Lambda     │  API calls   ┌──────────────────┐
│  Alexa   │────────────▶│  (ask-sdk +     │─────────────▶│  Plex Media      │
│  Device  │◀────────────│   plexapi)      │◀─────────────│  Server          │
└──────────┘  Directives └─────────────────┘              └──────────────────┘
     │                                                            ▲
     │         ┌──────────────────┐                               │
     └────────▶│  CloudFront CDN  │───────────────────────────────┘
    Audio      │  (port 443)      │  Origin: Plex (port 32400)
    Stream     └──────────────────┘
```

1. **Alexa** sends voice intents to the Lambda function
2. **Lambda** searches Plex via its API and returns AudioPlayer directives
3. **Alexa** streams audio through CloudFront, which fetches from Plex on port 32400

CloudFront is required because Alexa devices only stream from port 443 with trusted TLS certificates. CloudFront provides both, and is free for personal use (1TB/month free tier).

## Prerequisites

- An AWS account
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) installed
- A Plex Media Server with:
  - A music library
  - Remote access enabled (Plex Settings → Remote Access)
- An [Amazon Developer account](https://developer.amazon.com/)
- Docker (for building the Lambda package via SAM)

## Setup

### 1. Get Your Plex Token

1. Open Plex Web App and sign in
2. Navigate to any media item and click "Get Info" → "View XML"
3. In the URL bar, find `X-Plex-Token=YOUR_TOKEN`
4. Alternatively, check your Plex `Preferences.xml` for `PlexOnlineToken`

### 2. Find Your Plex Direct URL

Your Plex server's external HTTPS URL is needed for CloudFront. Find it by running:

```bash
curl -s "https://plex.tv/api/resources?X-Plex-Token=YOUR_TOKEN" | grep -o 'uri="https://[^"]*plex.direct[^"]*"'
```

Or construct it from your Plex server's `Preferences.xml`:
- Find `CertificateUUID` (e.g., `abc123def456abc123def456abc123de`)
- Your public IP with dashes (e.g., `192.168.1.100` → `192-168-1-100`)
- URL format: `https://{IP-WITH-DASHES}.{CERTIFICATE-UUID}.plex.direct:32400`

### 3. Create CloudFront Distribution

CloudFront acts as a CDN between Alexa and your Plex server, providing a trusted HTTPS endpoint on port 443.

```bash
aws cloudfront create-distribution --distribution-config '{
  "CallerReference": "plex-music-'$(date +%s)'",
  "Origins": {
    "Quantity": 1,
    "Items": [{
      "Id": "plex-origin",
      "DomainName": "YOUR-IP-DASHES.YOUR-CERT-UUID.plex.direct",
      "CustomOriginConfig": {
        "HTTPPort": 80,
        "HTTPSPort": 32400,
        "OriginProtocolPolicy": "https-only",
        "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]}
      }
    }]
  },
  "DefaultCacheBehavior": {
    "TargetOriginId": "plex-origin",
    "ViewerProtocolPolicy": "https-only",
    "AllowedMethods": {"Quantity": 3, "Items": ["GET", "HEAD", "OPTIONS"], "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
    "ForwardedValues": {"QueryString": true, "Cookies": {"Forward": "none"}},
    "MinTTL": 0,
    "DefaultTTL": 86400,
    "MaxTTL": 31536000,
    "Compress": false
  },
  "Enabled": true,
  "Comment": "Plex Music Player for Alexa"
}'
```

Note the `DomainName` in the output (e.g., `d1234abcdef.cloudfront.net`). This is your `STREAM_BASE_URL`.

Wait a few minutes for the distribution to deploy (status changes from `InProgress` to `Deployed`).

### 4. Create the Alexa Skill

1. Go to the [Alexa Developer Console](https://developer.amazon.com/alexa/console/ask)
2. Click "Create Skill"
3. Name: `Plex Music Player`
4. Model: Custom
5. Backend: Provision your own (Lambda)
6. Language: English (US)
7. After creation, go to **JSON Editor** under "Interaction Model" and paste the contents of `skill-package/interactionModels/custom/en-US.json`
8. Under **Interfaces**, enable **Audio Player**
9. **Build the model**
10. Note your **Skill ID** (shown at top of the skill page: `amzn1.ask.skill.xxx`)

### 5. Deploy the Lambda Function

```bash
# Set required environment variables
export PLEX_URL="https://YOUR-IP-DASHES.YOUR-CERT-UUID.plex.direct:32400"
export PLEX_TOKEN="your_plex_token"
export PLEX_MUSIC_LIBRARY="Music"
export ALEXA_SKILL_ID="amzn1.ask.skill.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# Deploy
./deploy.sh
```

After deployment, set the `STREAM_BASE_URL` environment variable on the Lambda:

```bash
aws lambda update-function-configuration \
  --function-name plexMusicPlayer \
  --region us-east-1 \
  --environment "Variables={PLEX_URL=$PLEX_URL,PLEX_TOKEN=$PLEX_TOKEN,PLEX_MUSIC_LIBRARY=$PLEX_MUSIC_LIBRARY,STREAM_BASE_URL=https://YOUR-CLOUDFRONT-DOMAIN.cloudfront.net,QUEUE_TABLE=plexMusicPlayer-queue}"
```

### 6. Connect the Skill to Lambda

1. Get the Lambda ARN:
   ```bash
   aws cloudformation describe-stacks \
     --stack-name plex-music-player \
     --region us-east-1 \
     --query 'Stacks[0].Outputs[?OutputKey==`FunctionArn`].OutputValue' \
     --output text
   ```
2. In the Alexa Developer Console, go to your skill → **Endpoint**
3. Select **AWS Lambda ARN**
4. Paste the ARN in the **Default Region** field
5. Save

### 7. Set Lambda Permissions

Allow Alexa to invoke your Lambda function:

```bash
aws lambda add-permission \
  --function-name plexMusicPlayer \
  --statement-id alexa-skill-invoke \
  --action lambda:InvokeFunction \
  --principal alexa-appkit.amazon.com \
  --region us-east-1
```

### 8. Test

- Say: "Alexa, open plex music"
- Say: "Alexa, ask plex music to play Wasted Years"
- Say: "Alexa, ask plex music to play Iron Maiden"
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
│   ├── queue_manager.py     # Playback queue management
│   └── requirements.txt     # Lambda dependencies
├── skill-package/
│   ├── interactionModels/
│   │   └── custom/
│   │       └── en-US.json   # Alexa interaction model
│   └── skill.json           # Skill manifest
├── template.yaml            # AWS SAM template
├── deploy.sh                # Deployment script
├── requirements.txt         # Local dev dependencies
├── .env.example             # Environment variable template
└── README.md
```

## Configuration

| Environment Variable | Required | Description |
|---------------------|----------|-------------|
| `PLEX_URL` | Yes | Your Plex server's HTTPS URL (e.g., `https://IP.uuid.plex.direct:32400`) |
| `PLEX_TOKEN` | Yes | Your Plex authentication token |
| `PLEX_MUSIC_LIBRARY` | No | Name of your music library section (default: `Music`) |
| `STREAM_BASE_URL` | Yes | Your CloudFront domain (e.g., `https://d1234abcdef.cloudfront.net`) |
| `QUEUE_TABLE` | No | DynamoDB table name for queue persistence (default: `plexMusicPlayer-queue`, auto-created by SAM) |

## Troubleshooting

### "I couldn't find that song/artist/album"
- Verify the track exists in your Plex music library
- Check that `PLEX_MUSIC_LIBRARY` matches your library name exactly (case-sensitive)
- For songs in compilation/loose folders, the skill searches by per-track artist (ID3 tag) as well as library-level artist
- Check Lambda CloudWatch logs: `aws logs tail /aws/lambda/plexMusicPlayer --region us-east-1 --since 5m`

### Audio won't play / playback fails
- Verify CloudFront can reach your Plex server: `curl -sI "https://YOUR-CF-DOMAIN.cloudfront.net/library/parts/PART_ID/file.mp3?X-Plex-Token=YOUR_TOKEN"`
- Ensure Plex remote access is enabled
- Check that the CloudFront distribution status is `Deployed`
- Alexa supports MP3 and AAC — FLAC files need transcoding (not yet implemented)

### "The requested skill did not provide a valid response"
- Ensure the Lambda has permission for Alexa to invoke it (step 7)
- Check that the Alexa Skill ID matches what was deployed

### Lambda timeout errors
- The default timeout is 10 seconds; increase if your Plex server is slow to respond
- First invocations after idle periods have cold start overhead (~1-2 seconds)

### "There was an error connecting to your Plex server"
- Verify `PLEX_URL` and `PLEX_TOKEN` are correct
- Ensure Plex remote access is enabled and the server is reachable from the internet
- Test: `curl -s "YOUR_PLEX_URL/library/sections?X-Plex-Token=YOUR_TOKEN"`

## Development

### Local Testing

```bash
# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set environment variables
export PLEX_URL="https://your-server.plex.direct:32400"
export PLEX_TOKEN="your_token"
export STREAM_BASE_URL="https://your-cf-domain.cloudfront.net"

# Test Plex connectivity
python3 -c "
from lambda.plex_client import PlexMusicClient
client = PlexMusicClient()
print(f'Connected to: {client.server.friendlyName}')
print(f'Music library: {client.music_library.title}')
print(f'Tracks: {client.music_library.totalSize}')
"
```

## Cost

This project uses the following AWS services:

- **Lambda** — free tier covers 1M requests/month
- **CloudFront** — free tier covers 1TB transfer/month (~130 full albums of streaming)
- **CloudWatch Logs** — minimal cost for log storage

For personal use, this should remain within the free tier indefinitely.

## Limitations

- **No multi-user support** — uses a single Plex token (designed for personal use)
- **No persistent queue** — the playback queue resets if Lambda cold-starts mid-playback (for production use, add DynamoDB persistence)
- **Single music library** — searches one library section at a time
- **MP3/AAC only** — FLAC and other formats require transcoding (not yet implemented)

## Credits

Inspired by [Tyzer34/plexMusicPlayer](https://github.com/Tyzer34/plexMusicPlayer), rebuilt from scratch with modern tooling:
- [ask-sdk](https://github.com/alexa/alexa-skills-kit-sdk-for-python) instead of deprecated Flask-Ask
- [plexapi](https://github.com/pkkid/python-plexapi) for robust Plex integration
- AWS Lambda + SAM + CloudFront instead of Heroku

## License

GPL-3.0
