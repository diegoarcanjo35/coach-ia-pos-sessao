import json
import logging
import os
from pathlib import Path
import subprocess
import time

from redis import Redis
from PIL import Image
import numpy as np

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


def extract_timeline(video_path: str, duration: float) -> list[dict[str, object]]:
    frames_dir = Path(video_path).parent / "frames"
    frames_dir.mkdir(exist_ok=True)
    pattern = str(frames_dir / "frame-%06d.jpg")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video_path,
         "-vf", "fps=1/2,scale=360:-2", "-q:v", "4", pattern],
        check=True, capture_output=True, text=True, timeout=max(180, int(duration * 2)),
    )
    frames = sorted(frames_dir.glob("frame-*.jpg"))
    timeline: list[dict[str, object]] = []
    previous_histogram: list[float] | None = None
    segment_id = 1
    for index, frame in enumerate(frames, start=1):
        with Image.open(frame) as image:
            histogram = image.convert("L").resize((64, 64)).histogram()
        total = float(sum(histogram)) or 1.0
        normalized = [value / total for value in histogram]
        change_score = 0.0 if previous_histogram is None else sum(abs(a - b) for a, b in zip(normalized, previous_histogram)) / 2
        is_transition = previous_histogram is not None and change_score >= 0.18
        if is_transition:
            segment_id += 1
        timeline.append({
            "index": index, "timestamp_seconds": round((index - 1) * 2.0, 3), "file": frame.name,
            "screen_type": "transition" if is_transition else "unknown", "confidence": round(change_score, 4),
            "change_score": round(change_score, 4), "segment_id": segment_id,
        })
        previous_histogram = normalized
    return timeline


def classify_pppoker_frame(frame_path: Path, transition: bool) -> dict[str, object]:
    with Image.open(frame_path) as source:
        image = np.asarray(source.convert("RGB"))
    height, width = image.shape[:2]
    if height / max(width, 1) < 1.45:
        return {"screen_type": "unknown", "confidence": 0.0, "evidence": {"layout": "not_vertical_pppoker"}}
    panel = image[int(height * .07):int(height * .79), int(width * .18):int(width * .98)]
    lum = float(panel.mean())
    blue = float(((panel[:, :, 2] > panel[:, :, 0] * 1.08) & (panel[:, :, 2] > panel[:, :, 1] * 1.05)).mean())
    hero = image[int(height * .79):int(height * .90), int(width * .34):int(width * .75)]
    white = float(((hero[:, :, 0] > 165) & (hero[:, :, 1] > 165) & (hero[:, :, 2] > 165)).mean())
    evidence = {"layout": "pppoker_vertical_v220", "panel_luminance": round(lum, 2), "panel_blue_ratio": round(blue, 4), "hero_white_ratio": round(white, 4)}
    if lum < 58 and blue > .82:
        return {"screen_type": "lobby", "confidence": round(min(.98, .75 + (blue - .82) * 2), 3), "evidence": evidence}
    if blue >= .75 and 70 <= lum <= 140:
        return {"screen_type": "table", "confidence": round(min(.96, .76 + (blue - .75)), 3), "evidence": evidence}
    if transition:
        return {"screen_type": "transition", "confidence": .65, "evidence": evidence}
    return {"screen_type": "unknown", "confidence": 0.0, "evidence": evidence}


def detect_hand_boundaries(video_path: str, width: int, height: int, duration: float, fps: float = 2.0) -> dict[str, object]:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video_path, "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    frame_bytes = width * height * 3
    samples: list[dict[str, object]] = []
    index = 0
    assert process.stdout is not None
    while True:
        raw = process.stdout.read(frame_bytes)
        if len(raw) != frame_bytes:
            break
        image = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
        hero = image[int(height * .79):int(height * .90), int(width * .34):int(width * .75)]
        white = float(((hero[:, :, 0] > 165) & (hero[:, :, 1] > 165) & (hero[:, :, 2] > 165)).mean())
        panel = image[int(height * .07):int(height * .79), int(width * .18):int(width * .98)]
        luminance = float(panel.mean())
        blue = float(((panel[:, :, 2] > panel[:, :, 0] * 1.08) & (panel[:, :, 2] > panel[:, :, 1] * 1.05)).mean())
        lobby = luminance < 58 and blue > .82
        obstructed = not lobby and (blue < .75 or luminance < 70 or luminance > 140)
        samples.append({"time": index / fps, "cards": white >= .12, "lobby": lobby, "obstructed": obstructed})
        index += 1
    process.wait()

    starts: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    absent = 0
    lobby_gap = False
    obstruction_gap = False
    if samples and samples[0]["cards"]:
        starts.append({"time": 0.0, "partial": True, "confidence": .72})
    for position in range(1, len(samples)):
        sample = samples[position]
        if not sample["cards"]:
            if sample["lobby"]: lobby_gap = True
            elif sample["obstructed"]: obstruction_gap = True
            else: absent += 1
            continue
        if not samples[position - 1]["cards"]:
            gap = absent / fps
            if gap >= 1.0 and not lobby_gap and not obstruction_gap:
                confidence = min(.98, .82 + min(gap, 4) * .04)
                starts.append({"time": float(sample["time"]), "partial": False, "confidence": round(confidence, 3)})
            elif lobby_gap:
                events.append({"time": sample["time"], "type": "lobby_closed_resume"})
            elif obstruction_gap:
                events.append({"time": sample["time"], "type": "obstruction_resume"})
        absent = 0; lobby_gap = False; obstruction_gap = False

    lobby_times = [float(item["time"]) for item in samples if item["lobby"]]
    hands: list[dict[str, object]] = []
    for hand_index, start in enumerate(starts, start=1):
        start_time = float(start["time"])
        next_time = float(starts[hand_index]["time"]) if hand_index < len(starts) else duration
        end_time = max(start_time + 1, min(duration, next_time - .5 if hand_index < len(starts) else duration))
        lobby_overlap = any(start_time <= time <= end_time for time in lobby_times)
        hands.append({"index": hand_index, "start_seconds": round(start_time, 3), "end_seconds": round(end_time, 3),
                      "confidence": round(float(start["confidence"]) - (.08 if lobby_overlap else 0), 3),
                      "partial": bool(start["partial"]), "lobby_during_hand": lobby_overlap,
                      "status": "review" if start["partial"] or lobby_overlap else "detected"})
    return {"sample_fps": fps, "sample_count": len(samples), "hands": hands, "events": events,
            "summary": {"hands_detected": len(hands), "partial": sum(bool(item["partial"]) for item in hands),
                        "with_lobby": sum(bool(item["lobby_during_hand"]) for item in hands)}}


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
            write_manifest(video_path, "segmenting", session_id=job.get("id"), metadata=metadata)
            timeline = extract_timeline(video_path, float(metadata["duration_seconds"]))
            frames_dir = Path(video_path).parent / "frames"
            for item in timeline:
                result = classify_pppoker_frame(frames_dir / str(item["file"]), item["screen_type"] == "transition")
                item.update(result)
            counts = {name: sum(item["screen_type"] == name for item in timeline) for name in ("table", "lobby", "transition", "unknown")}
            hand_detection = detect_hand_boundaries(video_path, int(metadata["video"]["width"]), int(metadata["video"]["height"]), float(metadata["duration_seconds"])) if counts["table"] else None
            write_manifest(video_path, "hands_detected", session_id=job.get("id"), metadata=metadata, timeline=timeline,
                           segmentation={"interval_seconds": 2, "frame_count": len(timeline),
                                         "segment_count": max((int(item["segment_id"]) for item in timeline), default=0),
                                         "transition_threshold": 0.18, "policy": "unknown_until_evidenced"},
                           classification={"engine": "pppoker_vertical_v220", "counts": counts, "low_confidence_requires_review": True},
                           hand_detection=hand_detection)
            logging.info("Vídeo segmentado: %s (%s frames)", job.get("id"), len(timeline))
        except Exception as exc:
            write_manifest(video_path, "failed", session_id=job.get("id"), error=str(exc))
            logging.exception("Falha ao validar vídeo %s", job.get("id"))


if __name__ == "__main__":
    main()
