from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Literal
import re

app = FastAPI(title="Bex Pause Speech API", version="1.2.0")


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
    ssml_text = text
    normalized_text = text

    def add_pause(source: str, position: int, duration_ms: int, pause_type: str):
        pauses.append({
            "source": source,
            "position": position,
            "durationMs": duration_ms,
            "type": pause_type
        })

    # 1) Explizite Pausen wie [pause:800ms] oder [pause:1s]
    explicit_pattern = r"\[pause:(\d+)(ms|s)\]"

    def replace_explicit_pause(match):
        full = match.group(0)
        value = match.group(1)
        unit = match.group(2)
        pos = match.start()

        duration_ms = int(value)
        if unit == "s":
            duration_ms *= 1000

        add_pause(full, pos, duration_ms, "explicit")
        return f"<break time=\"{duration_ms}ms\"/>"

    ssml_text = re.sub(explicit_pattern, replace_explicit_pause, ssml_text)
    normalized_text = re.sub(explicit_pattern, "", normalized_text)

    # 2) Leere Klammern [] als 1 Sekunde Pause
    empty_pause_pattern = r"\[\]"

    def replace_empty_pause(match):
        full = match.group(0)
        pos = match.start()

        add_pause(full, pos, 1000, "explicit")
        return "<break time=\"1000ms\"/>"

    ssml_text = re.sub(empty_pause_pattern, replace_empty_pause, ssml_text)
    normalized_text = re.sub(empty_pause_pattern, "", normalized_text)

    # 3) Ellipsen ... als kurze Pause
    ellipsis_pattern = r"\.\.\.+"

    def replace_ellipsis(match):
        full = match.group(0)
        pos = match.start()

        add_pause(full, pos, 400, "ellipsis")
        return "<break time=\"400ms\"/>"

    ssml_text = re.sub(ellipsis_pattern, replace_ellipsis, ssml_text)
    normalized_text = re.sub(ellipsis_pattern, "", normalized_text)

    # 4) Absatzwechsel (mindestens eine Leerzeile) als längere Pause
    paragraph_pattern = r"(?:\r?\n\s*){2,}"

    def replace_paragraph(match):
        full = match.group(0)
        pos = match.start()

        add_pause("paragraph", pos, 900, "paragraph")
        return "<break time=\"900ms\"/>"

    ssml_text = re.sub(paragraph_pattern, replace_paragraph, ssml_text)
    normalized_text = re.sub(paragraph_pattern, " ", normalized_text)

    # 5) Einfache Zeilenumbrüche als kurze Pause
    linebreak_pattern = r"\r?\n"

    def replace_linebreak(match):
        full = match.group(0)
        pos = match.start()

        add_pause("linebreak", pos, 250, "linebreak")
        return "<break time=\"250ms\"/>"

    ssml_text = re.sub(linebreak_pattern, replace_linebreak, ssml_text)
    normalized_text = re.sub(linebreak_pattern, " ", normalized_text)

    # Text bereinigen
    normalized_text = re.sub(r"\s+", " ", normalized_text).strip()
    ssml_text = re.sub(r"\s+", " ", ssml_text).strip()

    return normalized_text, ssml_text, pauses


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
