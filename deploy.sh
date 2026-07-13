#!/bin/bash
set -eo pipefail

# plexMusicPlayer deployment script
# Uses AWS SAM CLI to build and deploy the Lambda function

STACK_NAME="${STACK_NAME:-plex-music-player}"
REGION="${AWS_REGION:-us-east-1}"
S3_BUCKET="${SAM_S3_BUCKET:-}"

echo "=== plexMusicPlayer Deployment ==="
echo "Stack: $STACK_NAME"
echo "Region: $REGION"
echo ""

# Check prerequisites
command -v sam >/dev/null 2>&1 || { echo "Error: AWS SAM CLI is required. Install from https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"; exit 1; }
command -v aws >/dev/null 2>&1 || { echo "Error: AWS CLI is required."; exit 1; }

# Prompt for missing environment variables
if [ -z "${PLEX_URL:-}" ]; then
    read -rp "Enter your Plex server URL (e.g., https://your-server.plex.direct:32400): " PLEX_URL
fi
if [ -z "${PLEX_TOKEN:-}" ]; then
    read -rp "Enter your Plex token: " PLEX_TOKEN
fi
if [ -z "${ALEXA_SKILL_ID:-}" ]; then
    read -rp "Enter your Alexa Skill ID (amzn1.ask.skill.xxx): " ALEXA_SKILL_ID
fi

# Build the Lambda package
echo "Building Lambda package..."
sam build --template-file template.yaml --use-container

# Deploy
echo "Deploying to AWS..."
if [ -n "$S3_BUCKET" ]; then
    sam deploy \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --s3-bucket "$S3_BUCKET" \
        --capabilities CAPABILITY_IAM \
        --parameter-overrides \
            PlexUrl="${PLEX_URL}" \
            PlexToken="${PLEX_TOKEN}" \
            PlexMusicLibrary="${PLEX_MUSIC_LIBRARY:-Music}" \
            AlexaSkillId="${ALEXA_SKILL_ID}" \
        --no-confirm-changeset
else
    sam deploy \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --resolve-s3 \
        --capabilities CAPABILITY_IAM \
        --parameter-overrides \
            PlexUrl="${PLEX_URL}" \
            PlexToken="${PLEX_TOKEN}" \
            PlexMusicLibrary="${PLEX_MUSIC_LIBRARY:-Music}" \
            AlexaSkillId="${ALEXA_SKILL_ID}" \
        --no-confirm-changeset
fi

echo ""
echo "=== Deployment Complete ==="
echo "Get the Lambda ARN with:"
echo "  aws cloudformation describe-stacks --stack-name $STACK_NAME --query 'Stacks[0].Outputs' --region $REGION"
