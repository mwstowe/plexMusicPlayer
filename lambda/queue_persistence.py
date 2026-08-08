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


def save_queue(device_id, queue):
    """Save the current queue state to DynamoDB.

    Stores track rating keys (not full objects) so the queue
    can be restored on cold start by re-fetching from Plex.

    Uses queue.get_all_keys() which returns the full key list
    even if only a subset of tracks are loaded in memory.

    Keyed by device_id so each Alexa device has its own independent queue.
    """
    track_keys = queue.get_all_keys()
    if not track_keys:
        # Delete the item if queue is empty
        try:
            table.delete_item(Key={"device_id": device_id})
        except Exception as e:
            logger.warning("Failed to delete queue: %s", e)
        return

    ttl = int(time.time()) + (TTL_HOURS * 3600)

    try:
        table.put_item(Item={
            "device_id": device_id,
            "track_keys": track_keys,
            "current_index": queue.absolute_index,
            "shuffle_enabled": queue.shuffle_enabled,
            "loop_enabled": queue.loop_enabled,
            "pause_offset_ms": queue.pause_offset_ms,
            "ttl": ttl,
        })
        logger.info("Saved queue: %d tracks, index %d", len(track_keys), queue.absolute_index)
    except Exception as e:
        logger.error("Failed to save queue: %s", e)


def load_queue(device_id):
    """Load queue state from DynamoDB for a specific device.

    Returns a dict with track_keys, current_index, shuffle_enabled,
    loop_enabled — or None if no saved state exists.
    """
    try:
        response = table.get_item(Key={"device_id": device_id})
        item = response.get("Item")
        if not item:
            logger.info("No item found in DynamoDB for device")
            return None

        track_keys = item.get("track_keys", [])
        logger.info("Loaded %d track keys from DynamoDB, current_index=%s",
                    len(track_keys), item.get("current_index"))

        return {
            "track_keys": track_keys,
            "current_index": int(item.get("current_index", 0)),
            "shuffle_enabled": item.get("shuffle_enabled", False),
            "loop_enabled": item.get("loop_enabled", False),
            "pause_offset_ms": int(item.get("pause_offset_ms", 0)),
        }
    except Exception as e:
        logger.error("Failed to load queue: %s", e, exc_info=True)
        return None


def update_index(device_id, current_index, pause_offset_ms=None):
    """Update just the current index and optionally pause offset (lightweight update)."""
    try:
        update_expr = "SET current_index = :idx, #ttl = :ttl"
        attr_values = {
            ":idx": current_index,
            ":ttl": int(time.time()) + (TTL_HOURS * 3600),
        }
        if pause_offset_ms is not None:
            update_expr += ", pause_offset_ms = :offset"
            attr_values[":offset"] = pause_offset_ms

        table.update_item(
            Key={"device_id": device_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues=attr_values,
        )
    except Exception as e:
        logger.error("Failed to update index: %s", e)
