import base64
import os
from pathlib import Path
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field
from PIL import Image
import numpy as np


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
    output_dir = Path(video_path).parent / "ai-evidence"
    output_dir.mkdir(exist_ok=True)
    candidates_dir = output_dir / f".candidates-{int(hand['index']):03d}"
    shutil.rmtree(candidates_dir, ignore_errors=True); candidates_dir.mkdir()
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-ss",str(start),"-i",video_path,
                    "-t",str(duration),"-vf","fps=1,scale=720:-2","-q:v","3",str(candidates_dir/"candidate-%04d.jpg")],
                   check=True, capture_output=True, timeout=max(90, int(duration*2)))
    candidates = sorted(candidates_dir.glob("candidate-*.jpg"))
    if not candidates: raise RuntimeError("Nenhum frame candidato extraído")
    max_frames = max(5, min(int(os.getenv("AI_FRAMES_PER_HAND", "9")), 12, len(candidates)))
    scores = [0.0]
    previous = None
    for path in candidates:
        with Image.open(path) as image:
            array = np.asarray(image.convert("L").resize((80,120)), dtype=np.float32)
        scores[-1] = 0.0 if previous is None else float(np.mean(np.abs(array-previous))/255)
        previous = array
        scores.append(0.0)
    scores = scores[:len(candidates)]
    fixed = {0,len(candidates)-1,len(candidates)//4,len(candidates)//2,(len(candidates)*3)//4}
    ranked = sorted(range(len(candidates)), key=lambda i:scores[i], reverse=True)
    selected = set(fixed)
    for index in ranked:
        if len(selected)>=max_frames: break
        selected.add(index)
    evidence = []
    for output_index, candidate_index in enumerate(sorted(selected), start=1):
        timestamp = round(min(end, start+candidate_index),3)
        filename = f"hand-{int(hand['index']):03d}-frame-{output_index:02d}.jpg"
        destination = output_dir / filename
        shutil.copy2(candidates[candidate_index],destination)
        evidence.append({"file":filename,"timestamp_seconds":timestamp,"change_score":round(scores[candidate_index],4),
                         "selection_reason":"boundary_or_quartile" if candidate_index in fixed else "visual_change"})
    shutil.rmtree(candidates_dir, ignore_errors=True)
    return evidence


def validate_extraction(extraction: HandExtraction, evidence: list[dict[str, object]]) -> dict[str, object]:
    issues=[]; evidence_files={str(item["file"]) for item in evidence}
    hero=[(c.rank,c.suit) for c in extraction.hero_cards if c.rank!="unknown" and c.suit!="unknown"]
    if len(hero)!=len(set(hero)): issues.append("duplicate_hero_card")
    expected={"flop":3,"turn":4,"river":5}
    previous:set[tuple[str,str]]=set()
    for snapshot in extraction.snapshots:
        board=[(c.rank,c.suit) for c in snapshot.board if c.rank!="unknown" and c.suit!="unknown"]
        if len(board)!=len(set(board)): issues.append(f"duplicate_board_card_{snapshot.street}")
        if snapshot.street in expected and len(board) not in (0,expected[snapshot.street]): issues.append(f"invalid_{snapshot.street}_card_count")
        current=set(board)
        if snapshot.street in expected and previous and not previous.issubset(current): issues.append(f"non_monotonic_board_{snapshot.street}")
        if snapshot.street in expected and current: previous=current
        if any(c.evidence_frame not in evidence_files for c in snapshot.board): issues.append("invalid_board_evidence_reference")
    if set(hero)&previous: issues.append("hero_card_also_on_board")
    if any(c.evidence_frame not in evidence_files for c in extraction.hero_cards): issues.append("invalid_hero_evidence_reference")
    if extraction.rabbit_runout_visible and not any(s.street=="rabbit" for s in extraction.snapshots): issues.append("rabbit_visible_without_rabbit_snapshot")
    components=[len(extraction.hero_cards)==2, extraction.hero_position not in ("","unknown"), bool(extraction.effective_stack_text),
                any(s.board for s in extraction.snapshots if s.street!="rabbit"), bool(extraction.actions)]
    completeness=round(sum(components)/len(components),2)
    if issues:
        extraction.requires_review=True; extraction.overall_confidence=min(extraction.overall_confidence,.55)
        extraction.warnings=list(dict.fromkeys(extraction.warnings+issues))
    return {"valid":not issues,"issues":issues,"completeness_score":completeness,"evidence_frames":len(evidence),
            "visual_change_frames":sum(item["selection_reason"]=="visual_change" for item in evidence)}


def analyze_hand(video_path: str, hand: dict[str, object], model: str,
                 progress_callback: Callable[[str], None] | None = None) -> dict[str, object]:
    if progress_callback: progress_callback("preparing_frames")
    evidence = extract_hand_frames(video_path, hand)
    content: list[dict[str, object]] = [{"type":"input_text","text":SYSTEM_PROMPT + "\nAnalyze these frames in chronological order."}]
    for item in evidence:
        path = Path(video_path).parent / "ai-evidence" / str(item["file"])
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type":"input_text","text":f"Frame {item['file']} at {item['timestamp_seconds']} seconds"})
        content.append({"type":"input_image","image_url":f"data:image/jpeg;base64,{encoded}","detail":"high"})
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=120, max_retries=2)
    if progress_callback: progress_callback("waiting_openai")
    response = client.responses.parse(model=model, reasoning={"effort":"low"}, store=False,
                                      input=[{"role":"user","content":content}], text_format=HandExtraction)
    if response.output_parsed is None:
        raise RuntimeError("A IA não retornou extração estruturada")
    if progress_callback: progress_callback("validating_response")
    calibration=validate_extraction(response.output_parsed,evidence)
    result = response.output_parsed.model_dump(mode="json")
    usage=response.usage.model_dump(mode="json") if response.usage else {}
    return {"hand_index":hand["index"],"status":"extracted","model":model,"response_id":response.id,
            "evidence":evidence,"extraction":result,"calibration":calibration,"usage":usage}


def analyze_session(video_path: str, hands: list[dict[str, object]],
                    progress_callback: Callable[[dict[str, object]], None] | None = None) -> dict[str, object]:
    provider = os.getenv("AI_PROVIDER", "none").lower()
    model = os.getenv("AI_MODEL", "gpt-5.6-sol")
    if provider != "openai" or not os.getenv("OPENAI_API_KEY"):
        return {"status":"disabled", "provider":provider, "model":model, "hands":[],
                "message":"Configure AI_PROVIDER=openai and OPENAI_API_KEY."}
    limit = max(1, min(int(os.getenv("AI_MAX_HANDS_PER_SESSION", "20")), 100))
    selected_hands=[item for item in hands if not bool(item.get("partial"))][:limit]
    results=[]; started=time.monotonic()
    def publish(current:int, phase:str) -> None:
        if not progress_callback: return
        completed=len(results); elapsed=time.monotonic()-started
        average=elapsed/completed if completed else 0
        remaining=round(average*(len(selected_hands)-completed)) if average else None
        phase_weight={"preparing_frames":.15,"waiting_openai":.45,"validating_response":.85,"hand_completed":0}.get(phase,0)
        percent=round(((completed+phase_weight)/max(len(selected_hands),1))*100)
        progress_callback({"total_hands":len(selected_hands),"completed_hands":completed,"current_hand":current,
                           "phase":phase,"percent":min(percent,99) if phase!="completed" else 100,
                           "extracted":sum(x["status"]=="extracted" for x in results),
                           "errors":sum(x["status"]=="error" for x in results),"eta_seconds":remaining,
                           "elapsed_seconds":round(elapsed)})
    if progress_callback: publish(0,"starting")
    for hand in selected_hands:
        hand_index=int(hand["index"])
        try:
            results.append(analyze_hand(video_path,hand,model,lambda phase:publish(hand_index,phase)))
        except Exception as exc:
            results.append({"hand_index":hand["index"], "status":"error", "model":model,
                            "error":f"{type(exc).__name__}: {exc}"[:500]})
        publish(hand_index,"hand_completed")
    if progress_callback:
        progress_callback({"total_hands":len(selected_hands),"completed_hands":len(results),"current_hand":None,
                           "phase":"completed","percent":100,"extracted":sum(x["status"]=="extracted" for x in results),
                           "errors":sum(x["status"]=="error" for x in results),"eta_seconds":0,
                           "elapsed_seconds":round(time.monotonic()-started)})
    extracted = sum(item["status"] == "extracted" for item in results)
    input_tokens=sum(int(item.get("usage",{}).get("input_tokens",0)) for item in results)
    output_tokens=sum(int(item.get("usage",{}).get("output_tokens",0)) for item in results)
    return {"status":"completed_with_errors" if extracted < len(results) else "completed", "provider":"openai",
            "model":model, "hands":results, "summary":{"requested":len(results),"extracted":extracted,
            "errors":len(results)-extracted,"limited_to":limit,"input_tokens":input_tokens,"output_tokens":output_tokens},
            "policy":"post_session_evidence_only_no_strategy"}
