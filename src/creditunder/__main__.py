import asyncio
import structlog

from creditunder.observability import configure_logging
from creditunder.pipeline.processor import ApplicationProcessor


async def main():
    configure_logging()
    log = structlog.get_logger(__name__)
    log.info("creditunder.starting")
    processor = ApplicationProcessor()
    await processor.run()


if __name__ == "__main__":
    asyncio.run(main())
