from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Literal, Tuple
import re

app = FastAPI(title="Bex Pause Speech API", version="1.3.0")


class ConvertRequest(BaseModel):
    text: str
    language: str = "de-DE"
    outputMode: Literal["ssml", "text", "both"] = "both"
    contentType: Literal["default", "movement_story"] = "default"
    autoDetectSignals: bool = True
    signalPauseMs: int = 2200
    examplePauseMs: int = 1800


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
    detectedSignalWords: List[str] = []
    detectedExampleWords: List[str] = []


def extract_story_markers(text: str) -> Tuple[List[str], List[str]]:
    signal_words = []
    example_words = []

    signal_matches = re.findall(r"Wenn ich\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)\s+sage", text)
    example_matches = re.findall(r"Beispiel:\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)", text)

    for word in signal_matches:
        if word not in signal_words:
            signal_words.append(word)

    for word in example_matches:
        if word not in example_words:
            example_words.append(word)

    return signal_words, example_words


def insert_pause_after_word(text: str, word: str, break_tag: str) -> str:
    pattern = rf"\b({re.escape(word)})\b(?!\s*<break\b)"
    return re.sub(pattern, rf"\1{break_tag}", text)


def convert_pause_markup(
    text: str,
    content_type: str,
    auto_detect_signals: bool,
    signal_pause_ms: int,
    example_pause_ms: int
):
    pauses = []
    ssml_text = text
    normalized_text = text
    detected_signal_words: List[str] = []
    detected_example_words: List[str] = []

    def add_pause(source: str, position: int, duration_ms: int, pause_type: str):
        pauses.append({
            "source": source,
            "position": position,
            "durationMs": duration_ms,
            "type": pause_type
        })

    # 1) Explizite Pausen
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

    # 2) [] als 1 Sekunde
    empty_pause_pattern = r"\[\]"

    def replace_empty_pause(match):
        full = match.group(0)
        pos = match.start()
        add_pause(full, pos, 1000, "explicit")
        return "<break time=\"1000ms\"/>"

    ssml_text = re.sub(empty_pause_pattern, replace_empty_pause, ssml_text)
    normalized_text = re.sub(empty_pause_pattern, "", normalized_text)

    # 3) Ellipsen
    ellipsis_pattern = r"\.\.\.+"

    def replace_ellipsis(match):
        full = match.group(0)
        pos = match.start()
        add_pause(full, pos, 400, "ellipsis")
        return "<break time=\"400ms\"/>"

    ssml_text = re.sub(ellipsis_pattern, replace_ellipsis, ssml_text)
    normalized_text = re.sub(ellipsis_pattern, "", normalized_text)

    # 4) Absatzwechsel
    paragraph_pattern = r"(?:\r?\n\s*){2,}"

    def replace_paragraph(match):
        pos = match.start()
        add_pause("paragraph", pos, 900, "paragraph")
        return "<break time=\"900ms\"/>"

    ssml_text = re.sub(paragraph_pattern, replace_paragraph, ssml_text)
    normalized_text = re.sub(paragraph_pattern, " ", normalized_text)

    # 5) Einfache Zeilenumbrüche
    linebreak_pattern = r"\r?\n"

    def replace_linebreak(match):
        pos = match.start()
        add_pause("linebreak", pos, 250, "linebreak")
        return "<break time=\"250ms\"/>"

    ssml_text = re.sub(linebreak_pattern, replace_linebreak, ssml_text)
    normalized_text = re.sub(linebreak_pattern, " ", normalized_text)

    # 6) Automatische Signalwort-Erkennung für Bewegungsgeschichten
    if content_type == "movement_story" and auto_detect_signals:
        detected_signal_words, detected_example_words = extract_story_markers(text)

        for word in detected_signal_words:
            ssml_text = insert_pause_after_word(
                ssml_text,
                word,
                f'<break time="{signal_pause_ms}ms"/>'
            )
            add_pause(word, None if word not in text else text.find(word), signal_pause_ms, "signalword")

        for word in detected_example_words:
            ssml_text = insert_pause_after_word(
                ssml_text,
                word,
                f'<break time="{example_pause_ms}ms"/>'
            )
            add_pause(word, None if word not in text else text.find(word), example_pause_ms, "exampleword")

    normalized_text = re.sub(r"\s+", " ", normalized_text).strip()
    ssml_text = re.sub(r"\s+", " ", ssml_text).strip()

    return normalized_text, ssml_text, pauses, detected_signal_words, detected_example_words


@app.get("/")
def root():
    return {"message": "Bex Pause Speech API läuft."}


@app.post("/speech/convert", response_model=ConvertResponse)
def convert_speech(req: ConvertRequest):
    normalized_text, ssml_body, pauses, signal_words, example_words = convert_pause_markup(
        req.text,
        req.contentType,
        req.autoDetectSignals,
        req.signalPauseMs,
        req.examplePauseMs
    )

    ssml = f"<speak>{ssml_body}</speak>"

    if req.outputMode == "ssml":
        return ConvertResponse(
            ssml=ssml,
            detectedPauses=pauses,
            warnings=[],
            detectedSignalWords=signal_words,
            detectedExampleWords=example_words
        )

    if req.outputMode == "text":
        return ConvertResponse(
            normalizedText=normalized_text,
            detectedPauses=pauses,
            warnings=[],
            detectedSignalWords=signal_words,
            detectedExampleWords=example_words
        )

    return ConvertResponse(
        normalizedText=normalized_text,
        ssml=ssml,
        detectedPauses=pauses,
        warnings=[],
        detectedSignalWords=signal_words,
        detectedExampleWords=example_words
    )
