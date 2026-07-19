import json
from io import BytesIO
import logging
import os
from pathlib import Path
import subprocess
import time

from redis import Redis
from PIL import Image
import numpy as np
import re
import unicodedata

from ai_extractor import analyze_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
redis = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)


def write_manifest(video_path: str, status: str, **details: object) -> None:
    path = Path(video_path).parent / "manifest.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"status": status, "updated_at": time.time(), **details}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def normalize_ocr(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").upper()
    return re.sub(r"[^A-Z0-9 ]+", " ", value)


def read_evidence_text(image: Image.Image) -> dict[str, object]:
    enlarged = image.resize((image.width * 3, image.height * 3)).convert("L")
    payload = BytesIO()
    enlarged.save(payload, format="PNG")
    completed = subprocess.run(["tesseract", "stdin", "stdout", "-l", "por+eng", "--psm", "6"],
                               input=payload.getvalue(), capture_output=True, timeout=30)
    if completed.returncode:
        raise RuntimeError("OCR indisponível")
    raw = completed.stdout.decode("utf-8", errors="replace").strip()
    return {"raw_text": raw, "normalized_text": " ".join(normalize_ocr(raw).split()),
            "engine": "tesseract_por_eng_v1"}


def extract_lobby_candidates(text: str) -> dict[str, object]:
    """Extract only labeled tournament context; every value remains unverified."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").upper()
    result: dict[str, object] = {"verified": False, "source": "ocr"}
    players = re.search(r"(?:JOGADORES|PLAYERS)[^0-9]{0,24}(\d{1,5})\s*/\s*(\d{1,5})", normalized)
    average = re.search(r"(?:STACK MEDIO|MEDIA DE FICHAS|AVERAGE STACK)[^0-9]{0,24}([0-9]+(?:[.,][0-9]+)?)\s*BB", normalized)
    prize = re.search(r"(?:PREMIO|PREMIACAO|PRIZE)[^0-9]{0,24}([0-9][0-9.,]*)", normalized)
    remaining = re.search(r"(?:RESTANTES|REMAINING|LEFT)[^0-9]{0,16}(\d{1,5})", normalized)
    if players:
        result["players_current"], result["players_total"] = int(players.group(1)), int(players.group(2))
    if average:
        result["average_stack_bb"] = average.group(1).replace(",", ".")
    if prize:
        result["prize_text"] = prize.group(1)
    if remaining:
        result["players_remaining"] = int(remaining.group(1))
    result["fields_found"] = len(result) - 2
    return result


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
        is_last = hand_index == len(starts)
        last_cards = max((float(item["time"]) for item in samples if start_time <= float(item["time"]) <= end_time and item["cards"]), default=start_time)
        partial_end = is_last and duration - last_cards < 2.0
        partial_start = bool(start["partial"])
        reasons = []
        if partial_start: reasons.append("recording_started_during_hand")
        if partial_end: reasons.append("recording_ended_during_deal_or_hand")
        if lobby_overlap: reasons.append("lobby_during_hand")
        hands.append({"index": hand_index, "start_seconds": round(start_time, 3), "end_seconds": round(end_time, 3),
                      "duration_seconds": round(end_time - start_time, 3),
                      "confidence": round(float(start["confidence"]) - (.08 if lobby_overlap else 0), 3),
                      "partial": partial_start or partial_end, "partial_start": partial_start, "partial_end": partial_end,
                      "lobby_during_hand": lobby_overlap, "reasons": reasons,
                      "status": "quarantine" if partial_start or partial_end else ("review" if lobby_overlap else "detected")})
    complete = [item for item in hands if not item["partial"]]
    return {"sample_fps": fps, "sample_count": len(samples), "hands": hands, "events": events,
            "summary": {"candidates": len(hands), "complete_hands": len(complete), "partial": len(hands) - len(complete),
                        "with_lobby": sum(bool(item["lobby_during_hand"]) for item in hands)}}


def create_hand_clips(video_path: str, hands: list[dict[str, object]], duration: float) -> list[dict[str, object]]:
    output_dir = Path(video_path).parent / "clips"
    output_dir.mkdir(exist_ok=True)
    clips = []
    for hand in hands:
        start = max(0.0, float(hand["start_seconds"]) - 3.0)
        end = min(duration, float(hand["end_seconds"]))
        output = output_dir / f"hand-{int(hand['index']):03d}.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-i", video_path,
                        "-t", str(max(.5, end - start)), "-map", "0:v:0", "-an", "-c:v", "copy",
                        "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(output)],
                       check=True, capture_output=True, text=True, timeout=180)
        clips.append({"hand_index": hand["index"], "file": output.name, "start_seconds": round(start, 3), "end_seconds": round(end, 3),
                      "partial": hand["partial"], "publishable": not hand["partial"]})
    return clips


def build_context_evidence(video_path: str, timeline: list[dict[str, object]]) -> dict[str, object]:
    lobby_frames = [item for item in timeline if item["screen_type"] == "lobby"]
    lobby_events: list[dict[str, object]] = []
    for item in lobby_frames:
        timestamp = float(item["timestamp_seconds"])
        if not lobby_events or timestamp - float(lobby_events[-1]["end_seconds"]) > 2.1:
            lobby_events.append({"start_seconds": timestamp, "end_seconds": timestamp + 2.0, "frames": [item["file"]]})
        else:
            lobby_events[-1]["end_seconds"] = timestamp + 2.0
            lobby_events[-1]["frames"].append(item["file"])
    for event in lobby_events:
        event["duration_seconds"] = round(float(event["end_seconds"]) - float(event["start_seconds"]), 3)

    session_dir = Path(video_path).parent
    evidence_dir = session_dir / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    rabbit_candidates: list[dict[str, object]] = []
    for item in timeline:
        if item["screen_type"] != "table" or not (int(item["index"]) % 5 == 0 or float(item["change_score"]) >= .08):
            continue
        frame_path = session_dir / "frames" / str(item["file"])
        with Image.open(frame_path) as image:
            width, height = image.size
            crop = image.crop((int(width * .18), int(height * .34), int(width * .82), int(height * .46)))
            filename = f"rabbit-banner-{int(item['index']):06d}.jpg"
            crop.save(evidence_dir / filename, quality=88)
            try:
                ocr = read_evidence_text(crop)
            except Exception as exc:
                ocr = {"raw_text": "", "normalized_text": "", "engine": "unavailable", "error": type(exc).__name__}
        normalized = str(ocr["normalized_text"])
        confirmed = "PAGOU" in normalized and "COELHO" in normalized
        textual_signal = "PAGOU" in normalized or "COELHO" in normalized
        if textual_signal:
            rabbit_candidates.append({"timestamp_seconds": item["timestamp_seconds"], "file": filename,
                                      "status": "confirmed_text" if confirmed else "partial_text", "confirmed": confirmed,
                                      "ocr": ocr, "policy": "banner_text_required_before_confirmation"})
    for event in lobby_events:
        frame_path = session_dir / "frames" / str(event["frames"][0])
        try:
            with Image.open(frame_path) as image:
                event["ocr"] = {**read_evidence_text(image), "verified": False,
                                "policy": "raw_context_only_requires_review"}
                event["candidates"] = extract_lobby_candidates(str(event["ocr"]["raw_text"]))
        except Exception as exc:
            event["ocr"] = {"raw_text": "", "normalized_text": "", "verified": False,
                            "engine": "unavailable", "error": type(exc).__name__}
        event["representative_frame"] = event["frames"][0]
    confirmed_count = sum(bool(item["confirmed"]) for item in rabbit_candidates)
    return {"lobby_events": lobby_events,
            "rabbit_detection": {"confirmed_events": confirmed_count, "candidates": rabbit_candidates,
                                 "scanned_frames": sum(1 for item in timeline if item["screen_type"] == "table" and
                                                       (int(item["index"]) % 5 == 0 or float(item["change_score"]) >= .08)),
                                 "rule": "Only OCR containing PAGOU and COELHO may confirm an event."}}


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
            write_manifest(video_path, "classifying", session_id=job.get("id"), metadata=metadata, timeline=timeline)
            frames_dir = Path(video_path).parent / "frames"
            for item in timeline:
                result = classify_pppoker_frame(frames_dir / str(item["file"]), item["screen_type"] == "transition")
                item.update(result)
            counts = {name: sum(item["screen_type"] == name for item in timeline) for name in ("table", "lobby", "transition", "unknown")}
            common = {"session_id": job.get("id"), "metadata": metadata, "timeline": timeline,
                      "classification": {"engine": "pppoker_vertical_v220", "counts": counts, "low_confidence_requires_review": True}}
            write_manifest(video_path, "detecting_hands", **common)
            hand_detection = detect_hand_boundaries(video_path, int(metadata["video"]["width"]), int(metadata["video"]["height"]), float(metadata["duration_seconds"])) if counts["table"] else None
            write_manifest(video_path, "creating_clips", **common, hand_detection=hand_detection)
            clips = create_hand_clips(video_path, hand_detection["hands"], float(metadata["duration_seconds"])) if hand_detection else []
            write_manifest(video_path, "reading_context", **common, hand_detection=hand_detection, clips=clips)
            context = build_context_evidence(video_path, timeline)
            write_manifest(video_path, "ai_extracting", **common, hand_detection=hand_detection, clips=clips,
                           lobby_context=context["lobby_events"], rabbit_detection=context["rabbit_detection"])
            ai_analysis = analyze_session(video_path, hand_detection["hands"] if hand_detection else [])
            write_manifest(video_path, "clips_ready_for_review", session_id=job.get("id"), metadata=metadata, timeline=timeline,
                           segmentation={"interval_seconds": 2, "frame_count": len(timeline),
                                         "segment_count": max((int(item["segment_id"]) for item in timeline), default=0),
                                         "transition_threshold": 0.18, "policy": "unknown_until_evidenced"},
                           classification={"engine": "pppoker_vertical_v220", "counts": counts, "low_confidence_requires_review": True},
                           hand_detection=hand_detection, clips=clips,
                           lobby_context=context["lobby_events"], rabbit_detection=context["rabbit_detection"],
                           ai_analysis=ai_analysis)
            logging.info("Processamento concluído: %s (%s frames, %s clipes)", job.get("id"), len(timeline), len(clips))
        except Exception as exc:
            write_manifest(video_path, "failed", session_id=job.get("id"), error=str(exc))
            logging.exception("Falha ao validar vídeo %s", job.get("id"))


if __name__ == "__main__":
    main()
