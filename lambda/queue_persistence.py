"""DynamoDB persistence for the playback queue."""

import logging
import os
import time

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ.get("QUEUE_TABLE", "plexMusicPlayer-queue")
TTL_HOURS = 24

dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
table = dynamodb.Table(TABLE_NAME)
logger.info("Queue persistence initialized: table=%s", TABLE_NAME)


def save_queue(user_id, queue):
    """Save the current queue state to DynamoDB.

    Stores track rating keys (not full objects) so the queue
    can be restored on cold start by re-fetching from Plex.
    """
    if not queue.tracks:
        # Delete the item if queue is empty
        try:
            table.delete_item(Key={"user_id": user_id})
        except Exception as e:
            logger.warning("Failed to delete queue: %s", e)
        return

    track_keys = [str(t.ratingKey) for t in queue.tracks]
    ttl = int(time.time()) + (TTL_HOURS * 3600)

    try:
        table.put_item(Item={
            "user_id": user_id,
            "track_keys": track_keys,
            "current_index": queue.current_index,
            "shuffle_enabled": queue.shuffle_enabled,
            "loop_enabled": queue.loop_enabled,
            "ttl": ttl,
        })
        logger.info("Saved queue: %d tracks, index %d", len(track_keys), queue.current_index)
    except Exception as e:
        logger.error("Failed to save queue: %s", e)


def load_queue(user_id):
    """Load queue state from DynamoDB.

    Returns a dict with track_keys, current_index, shuffle_enabled,
    loop_enabled — or None if no saved state exists.
    """
    try:
        response = table.get_item(Key={"user_id": user_id})
        item = response.get("Item")
        if not item:
            logger.info("No item found in DynamoDB for user")
            return None

        track_keys = item.get("track_keys", [])
        logger.info("Loaded %d track keys from DynamoDB, current_index=%s",
                    len(track_keys), item.get("current_index"))

        return {
            "track_keys": track_keys,
            "current_index": int(item.get("current_index", 0)),
            "shuffle_enabled": item.get("shuffle_enabled", False),
            "loop_enabled": item.get("loop_enabled", False),
        }
    except Exception as e:
        logger.error("Failed to load queue: %s", e, exc_info=True)
        return None


def update_index(user_id, current_index):
    """Update just the current index (lightweight update for track advances)."""
    try:
        table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET current_index = :idx, #ttl = :ttl",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":idx": current_index,
                ":ttl": int(time.time()) + (TTL_HOURS * 3600),
            },
        )
    except Exception as e:
        logger.error("Failed to update index: %s", e)
