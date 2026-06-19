"""Background worker for sending notifications."""
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Worker started. Waiting for tasks...")
    while True:
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
