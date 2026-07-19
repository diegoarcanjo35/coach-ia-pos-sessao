import json
import logging
import os
import time

from redis import Redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
redis = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)


def main() -> None:
    logging.info("Worker pós-sessão iniciado; aguardando jobs.")
    while True:
        item = redis.blpop("coach-ia:jobs", timeout=10)
        if item is None:
            redis.set("coach-ia:worker:heartbeat", str(time.time()), ex=30)
            continue
        _, raw_job = item
        job = json.loads(raw_job)
        logging.info("Job recebido: %s", job.get("id", "sem-id"))
        # Adaptadores FFmpeg/OCR entram aqui. Nunca produzir análise em tempo real.


if __name__ == "__main__":
    main()
