from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Literal, Tuple
import re
import os
import uuid
import tempfile
import html
import azure.cognitiveservices.speech as speechsdk

app = FastAPI(title="Bex Pause Speech API", version="1.4.0")


class ConvertRequest(BaseModel):
    text: str
    language: str = "de-DE"
    outputMode: Literal["ssml", "text", "both"] = "both"
    contentType: Literal["default", "movement_story"] = "default"
    autoDetectSignals: bool = True
    signalPauseMs: int = 5000
    examplePauseMs: int = 4000
    ellipsisPauseMs: int = 500
    paragraphPauseMs: int = 1200
    linebreakPauseMs: int = 350


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


class SynthesizeRequest(BaseModel):
    ssml: Optional[str] = None
    text: Optional[str] = None
    voice: str = "de-DE-KatjaNeural"
    format: Literal["mp3"] = "mp3"


def extract_story_markers(text: str) -> Tuple[List[str], List[str]]:
    signal_words: List[str] = []
    example_words: List[str] = []

    signal_matches = re.findall(
        r"Wenn ich\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)\s+sage",
        text
    )
    example_matches = re.findall(
        r"Beispiel:\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)",
        text
    )

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
    example_pause_ms: int,
    ellipsis_pause_ms: int,
    paragraph_pause_ms: int,
    linebreak_pause_ms: int
):
    pauses = []
    ssml_text = text
    normalized_text = text
    detected_signal_words: List[str] = []
    detected_example_words: List[str] = []

    def add_pause(source: str, position: Optional[int], duration_ms: int, pause_type: str):
        pauses.append({
            "source": source,
            "position": position,
            "durationMs": duration_ms,
            "type": pause_type
        })

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

    empty_pause_pattern = r"\[\]"

    def replace_empty_pause(match):
        full = match.group(0)
        pos = match.start()
        add_pause(full, pos, 1000, "explicit")
        return "<break time=\"1000ms\"/>"

    ssml_text = re.sub(empty_pause_pattern, replace_empty_pause, ssml_text)
    normalized_text = re.sub(empty_pause_pattern, "", normalized_text)

    ellipsis_pattern = r"\.\.\.+"

    def replace_ellipsis(match):
        full = match.group(0)
        pos = match.start()
        add_pause(full, pos, ellipsis_pause_ms, "ellipsis")
        return f"<break time=\"{ellipsis_pause_ms}ms\"/>"

    ssml_text = re.sub(ellipsis_pattern, replace_ellipsis, ssml_text)
    normalized_text = re.sub(ellipsis_pattern, "", normalized_text)

    paragraph_pattern = r"(?:\r?\n\s*){2,}"

    def replace_paragraph(match):
        pos = match.start()
        add_pause("paragraph", pos, paragraph_pause_ms, "paragraph")
        return f"<break time=\"{paragraph_pause_ms}ms\"/>"

    ssml_text = re.sub(paragraph_pattern, replace_paragraph, ssml_text)
    normalized_text = re.sub(paragraph_pattern, " ", normalized_text)

    linebreak_pattern = r"\r?\n"

    def replace_linebreak(match):
        pos = match.start()
        add_pause("linebreak", pos, linebreak_pause_ms, "linebreak")
        return f"<break time=\"{linebreak_pause_ms}ms\"/>"

    ssml_text = re.sub(linebreak_pattern, replace_linebreak, ssml_text)
    normalized_text = re.sub(linebreak_pattern, " ", normalized_text)

    if content_type == "movement_story" and auto_detect_signals:
        detected_signal_words, detected_example_words = extract_story_markers(text)

        for word in detected_signal_words:
            ssml_text = insert_pause_after_word(
                ssml_text,
                word,
                f'<break time="{signal_pause_ms}ms"/>'
            )
            add_pause(word, text.find(word) if word in text else None, signal_pause_ms, "signalword")

        for word in detected_example_words:
            ssml_text = insert_pause_after_word(
                ssml_text,
                word,
                f'<break time="{example_pause_ms}ms"/>'
            )
            add_pause(word, text.find(word) if word in text else None, example_pause_ms, "exampleword")

    normalized_text = re.sub(r"\s+", " ", normalized_text).strip()
    ssml_text = re.sub(r"\s+", " ", ssml_text).strip()

    return normalized_text, ssml_text, pauses, detected_signal_words, detected_example_words


def build_voice_wrapped_ssml(ssml_or_text: str, voice: str, is_ssml: bool) -> str:
    if is_ssml:
        ssml = ssml_or_text.strip()
        if "<voice" in ssml:
            return ssml
        if ssml.startswith("<speak"):
            return ssml.replace(">", f'><voice name="{voice}">', 1).replace("</speak>", "</voice></speak>")
        return f'<speak version="1.0" xml:lang="de-DE"><voice name="{voice}">{ssml}</voice></speak>'

    safe_text = html.escape(ssml_or_text)
    return f'<speak version="1.0" xml:lang="de-DE"><voice name="{voice}">{safe_text}</voice></speak>'


def synthesize_ssml_to_mp3(ssml: str, voice: str) -> str:
    key = os.getenv("AZURE_SPEECH_KEY")
    region = os.getenv("AZURE_SPEECH_REGION")
    endpoint = os.getenv("AZURE_SPEECH_ENDPOINT")

    if not key:
        raise HTTPException(status_code=500, detail="AZURE_SPEECH_KEY fehlt.")

    if endpoint:
        speech_config = speechsdk.SpeechConfig(subscription=key, endpoint=endpoint)
    elif region:
        speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    else:
        raise HTTPException(status_code=500, detail="Azure Speech Region oder Endpoint fehlt.")
    speech_config.speech_synthesis_voice_name = voice
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz128KBitRateMonoMp3
    )

    filename = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.mp3")
    audio_config = speechsdk.audio.AudioOutputConfig(filename=filename)

    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config
    )

    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        details = getattr(result, "cancellation_details", None)
        message = "Audio-Synthese fehlgeschlagen."
        if details:
            message = f"{message} {details.reason}"
            if getattr(details, "error_details", None):
                message = f"{message} {details.error_details}"
        raise HTTPException(status_code=500, detail=message)

    return filename


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
        req.examplePauseMs,
        req.ellipsisPauseMs,
        req.paragraphPauseMs,
        req.linebreakPauseMs
    )

    ssml = f'<speak version="1.0" xml:lang="{req.language}"><voice name="de-DE-KatjaNeural">{ssml_body}</voice></speak>'

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


@app.post("/speech/synthesize")
def synthesize_speech(req: SynthesizeRequest):
    if not req.ssml and not req.text:
        raise HTTPException(status_code=400, detail="Entweder ssml oder text muss angegeben werden.")

    payload = req.ssml if req.ssml else req.text
    ssml = build_voice_wrapped_ssml(payload, req.voice, is_ssml=bool(req.ssml))
    filename = synthesize_ssml_to_mp3(ssml, req.voice)

    return FileResponse(
        filename,
        media_type="audio/mpeg",
        filename="bex-audio.mp3"
    )
