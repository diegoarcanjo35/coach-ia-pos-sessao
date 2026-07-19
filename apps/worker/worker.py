import json
import logging
import os
from pathlib import Path
import subprocess
import time

from redis import Redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
redis = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)


def write_manifest(video_path: str, status: str, **details: object) -> None:
    path = Path(video_path).parent / "manifest.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"status": status, "updated_at": time.time(), **details}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def probe_video(video_path: str) -> dict[str, object]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", video_path],
        check=True, capture_output=True, text=True, timeout=120,
    )
    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    if video_stream is None:
        raise ValueError("Nenhum stream de vídeo encontrado")
    format_data = payload.get("format", {})
    return {
        "duration_seconds": float(format_data.get("duration", 0)),
        "size_bytes": int(format_data.get("size", 0)),
        "format_name": format_data.get("format_name"),
        "video": {"codec": video_stream.get("codec_name"), "width": video_stream.get("width"), "height": video_stream.get("height"), "frame_rate": video_stream.get("avg_frame_rate")},
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
    }


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
        video_path = job["video_path"]
        try:
            write_manifest(video_path, "probing", session_id=job.get("id"))
            metadata = probe_video(video_path)
            write_manifest(video_path, "ready_for_segmentation", session_id=job.get("id"), metadata=metadata)
            logging.info("Vídeo validado: %s", job.get("id"))
        except Exception as exc:
            write_manifest(video_path, "failed", session_id=job.get("id"), error=str(exc))
            logging.exception("Falha ao validar vídeo %s", job.get("id"))


if __name__ == "__main__":
    main()
