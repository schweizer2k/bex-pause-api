from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Literal
import re

app = FastAPI(title="Bex Pause Speech API", version="1.1.0")


class ConvertRequest(BaseModel):
    text: str
    language: str = "de-DE"
    outputMode: Literal["ssml", "text", "both"] = "both"


class DetectedPause(BaseModel):
    source: str
    position: Optional[int] = None
    durationMs: int
    type: str


class ConvertResponse(BaseModel):
    normalizedText: Optional[str] = None
    ssml: Optional[str] = None
    detectedPauses: List[DetectedPause] = []
    warnings: List[str] = []


def convert_pause_markup(text: str):
    pauses = []
    working_text = text

    def replace_explicit_pause(match):
        full = match.group(0)
        value = match.group(1)
        unit = match.group(2)
        pos = match.start()

        duration_ms = int(value)
        if unit == "s":
            duration_ms *= 1000

        pauses.append({
            "source": full,
            "position": pos,
            "durationMs": duration_ms,
            "type": "explicit"
        })

        return f"<break time=\"{duration_ms}ms\"/>"

    def replace_empty_pause(match):
        full = match.group(0)
        pos = match.start()

        pauses.append({
            "source": full,
            "position": pos,
            "durationMs": 1000,
            "type": "explicit"
        })

        return "<break time=\"1000ms\"/>"

    explicit_pattern = r"\[pause:(\d+)(ms|s)\]"
    empty_pause_pattern = r"\[\]"

    working_text = re.sub(explicit_pattern, replace_explicit_pause, working_text)
    working_text = re.sub(empty_pause_pattern, replace_empty_pause, working_text)

    normalized_text = re.sub(explicit_pattern, "", text)
    normalized_text = re.sub(empty_pause_pattern, "", normalized_text)
    normalized_text = re.sub(r"\s+", " ", normalized_text).strip()

    return normalized_text, working_text, pauses


@app.get("/")
def root():
    return {"message": "Bex Pause Speech API läuft."}


@app.post("/speech/convert", response_model=ConvertResponse)
def convert_speech(req: ConvertRequest):
    normalized_text, ssml_body, pauses = convert_pause_markup(req.text)
    ssml = f"<speak>{ssml_body}</speak>"

    if req.outputMode == "ssml":
        return ConvertResponse(
            ssml=ssml,
            detectedPauses=pauses,
            warnings=[]
        )

    if req.outputMode == "text":
        return ConvertResponse(
            normalizedText=normalized_text,
            detectedPauses=pauses,
            warnings=[]
        )

    return ConvertResponse(
        normalizedText=normalized_text,
        ssml=ssml,
        detectedPauses=pauses,
        warnings=[]
    )
