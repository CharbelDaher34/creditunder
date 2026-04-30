"""
CRM mockup publisher — sends sample credit application events to Kafka.

Usage:
    uv run python -m mockups.crm.publisher
    uv run python -m mockups.crm.publisher --event-index 1   # send second event only
"""

import argparse
import asyncio
import json

from aiokafka import AIOKafkaProducer

from mockups.crm.sample_data import SAMPLE_EVENTS


async def publish(bootstrap_servers: str, topic: str, events: list[dict]) -> None:
    producer = AIOKafkaProducer(
        bootstrap_servers=bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
    )
    await producer.start()
    try:
        for event in events:
            key = event["application_id"]
            await producer.send_and_wait(topic, value=event, key=key)
            print(f"[CRM] Published event for application_id={event['application_id']}  event_id={event['event_id']}")
    finally:
        await producer.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="CRM mockup publisher")
    parser.add_argument("--bootstrap-servers", default="localhost:19092")
    parser.add_argument("--topic", default="credit-applications")
    parser.add_argument(
        "--event-index",
        type=int,
        default=None,
        help="Index of a single event to publish (0-based). Omit to publish all.",
    )
    args = parser.parse_args()

    if args.event_index is not None:
        events = [SAMPLE_EVENTS[args.event_index]]
    else:
        events = SAMPLE_EVENTS

    asyncio.run(publish(args.bootstrap_servers, args.topic, events))


if __name__ == "__main__":
    main()
