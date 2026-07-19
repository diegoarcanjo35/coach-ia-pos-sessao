import base64
import os
from pathlib import Path
import subprocess
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field


Rank = Literal["2","3","4","5","6","7","8","9","T","J","Q","K","A","unknown"]
Suit = Literal["spades","hearts","diamonds","clubs","unknown"]


class Card(BaseModel):
    rank: Rank
    suit: Suit
    confidence: float = Field(ge=0, le=1)
    evidence_frame: str


class PokerAction(BaseModel):
    street: Literal["preflop","flop","turn","river","showdown","unknown"]
    actor: str
    action: Literal["fold","check","call","bet","raise","all_in","unknown"]
    amount_text: str
    confidence: float = Field(ge=0, le=1)
    evidence_frame: str


class StreetSnapshot(BaseModel):
    street: Literal["preflop","flop","turn","river","showdown","rabbit","unknown"]
    board: list[Card]
    pot_text: str
    hero_stack_text: str
    timestamp_seconds: float
    evidence_frame: str


class HandExtraction(BaseModel):
    hero_cards: list[Card]
    hero_position: str
    effective_stack_text: str
    snapshots: list[StreetSnapshot]
    actions: list[PokerAction]
    showdown_visible: bool
    rabbit_runout_visible: bool
    overall_confidence: float = Field(ge=0, le=1)
    requires_review: bool
    warnings: list[str]


SYSTEM_PROMPT = """You extract visual evidence from an already completed PPPoker hand for post-session study.
Never give strategy, recommendations, ranges, or advice. Never infer hidden information.
Use only pixels visible in the supplied chronological frames. If a rank, suit, player, action, amount,
position, stack, or street is unclear, return unknown or an empty string and lower confidence.
Frames are labeled with filenames and timestamps. evidence_frame must be one supplied filename.
A rabbit runout (Portuguese banner containing PAGOU O COELHO) is not strategic action: mark it rabbit,
set rabbit_runout_visible, and never turn its cards into flop/turn/river actions.
Set requires_review when any important field is unclear or frames do not prove the full hand."""


def extract_hand_frames(video_path: str, hand: dict[str, object]) -> list[dict[str, object]]:
    start = float(hand["start_seconds"])
    end = float(hand["end_seconds"])
    duration = max(0.5, end - start)
    offsets = [min(1.0, duration * .08), duration * .25, duration * .5, duration * .75, max(.1, duration - .5)]
    timestamps = sorted({round(min(end, start + offset), 3) for offset in offsets})
    output_dir = Path(video_path).parent / "ai-evidence"
    output_dir.mkdir(exist_ok=True)
    evidence = []
    for index, timestamp in enumerate(timestamps, start=1):
        filename = f"hand-{int(hand['index']):03d}-frame-{index:02d}.jpg"
        destination = output_dir / filename
        subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-ss",str(timestamp),"-i",video_path,
                        "-frames:v","1","-q:v","2",str(destination)], check=True, capture_output=True, timeout=60)
        evidence.append({"file": filename, "timestamp_seconds": timestamp})
    return evidence


def analyze_hand(video_path: str, hand: dict[str, object], model: str) -> dict[str, object]:
    evidence = extract_hand_frames(video_path, hand)
    content: list[dict[str, object]] = [{"type":"input_text","text":SYSTEM_PROMPT + "\nAnalyze these frames in chronological order."}]
    for item in evidence:
        path = Path(video_path).parent / "ai-evidence" / str(item["file"])
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type":"input_text","text":f"Frame {item['file']} at {item['timestamp_seconds']} seconds"})
        content.append({"type":"input_image","image_url":f"data:image/jpeg;base64,{encoded}","detail":"high"})
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=120, max_retries=2)
    response = client.responses.parse(model=model, reasoning={"effort":"low"}, store=False,
                                      input=[{"role":"user","content":content}], text_format=HandExtraction)
    if response.output_parsed is None:
        raise RuntimeError("A IA não retornou extração estruturada")
    result = response.output_parsed.model_dump(mode="json")
    return {"hand_index": hand["index"], "status":"extracted", "model":model, "evidence":evidence, "extraction":result}


def analyze_session(video_path: str, hands: list[dict[str, object]]) -> dict[str, object]:
    provider = os.getenv("AI_PROVIDER", "none").lower()
    model = os.getenv("AI_MODEL", "gpt-5.6-sol")
    if provider != "openai" or not os.getenv("OPENAI_API_KEY"):
        return {"status":"disabled", "provider":provider, "model":model, "hands":[],
                "message":"Configure AI_PROVIDER=openai and OPENAI_API_KEY."}
    limit = max(1, min(int(os.getenv("AI_MAX_HANDS_PER_SESSION", "20")), 100))
    results = []
    for hand in [item for item in hands if not bool(item.get("partial"))][:limit]:
        try:
            results.append(analyze_hand(video_path, hand, model))
        except Exception as exc:
            results.append({"hand_index":hand["index"], "status":"error", "model":model,
                            "error":f"{type(exc).__name__}: {exc}"[:500]})
    extracted = sum(item["status"] == "extracted" for item in results)
    return {"status":"completed_with_errors" if extracted < len(results) else "completed", "provider":"openai",
            "model":model, "hands":results, "summary":{"requested":len(results),"extracted":extracted,
            "errors":len(results)-extracted,"limited_to":limit},
            "policy":"post_session_evidence_only_no_strategy"}
