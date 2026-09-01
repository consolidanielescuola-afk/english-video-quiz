"""
Validazione del video e recupero della trascrizione.

Due fonti dati separate, per due responsabilità diverse:
  1. YouTube Data API v3 -> esistenza, titolo, canale, durata esatta.
  2. youtube-transcript-api -> testo della trascrizione + lingua rilevata
     (usata come conferma "è davvero in inglese", più affidabile dei soli
     metadati del video, spesso incompleti).

Tutte le funzioni sollevano HTTPException con un messaggio in italiano,
pronto per essere mostrato così com'è nel form del frontend.
"""

import os
import re
from dataclasses import dataclass

import httpx
from fastapi import HTTPException
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
MAX_DURATION_SECONDS = 10 * 60  # requisito di progetto: max 10 minuti

VIDEO_ID_PATTERNS = [
    r"(?:v=|\/videos\/|embed\/|youtu\.be\/|\/v\/|\/e\/|watch\?v=)([a-zA-Z0-9_-]{11})",
]


@dataclass
class ValidatedVideo:
    id: str
    title: str
    channel: str
    duration_seconds: int
    transcript_text: str
    transcript_language: str


def extract_video_id(url: str) -> str:
    for pattern in VIDEO_ID_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise HTTPException(status_code=400, detail="L'URL inserito non sembra un link YouTube valido.")


def _parse_iso8601_duration(duration: str) -> int:
    """Converte 'PT9M32S' -> 572 (secondi), senza dipendenze esterne."""
    pattern = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not pattern:
        return 0
    hours, minutes, seconds = (int(g) if g else 0 for g in pattern.groups())
    return hours * 3600 + minutes * 60 + seconds


async def _fetch_video_metadata(video_id: str) -> dict:
    if not YOUTUBE_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="YOUTUBE_API_KEY non configurata sul server.",
        )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,contentDetails,status",
                "id": video_id,
                "key": YOUTUBE_API_KEY,
            },
        )
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])
    if not items:
        raise HTTPException(status_code=404, detail="Video non trovato: controlla che l'URL sia corretto e che il video sia pubblico.")

    item = items[0]
    status = item.get("status", {})
    if status.get("privacyStatus") == "private":
        raise HTTPException(status_code=404, detail="Il video è privato e non può essere utilizzato.")

    snippet = item["snippet"]
    duration_seconds = _parse_iso8601_duration(item["contentDetails"]["duration"])

    return {
        "title": snippet["title"],
        "channel": snippet["channelTitle"],
        "duration_seconds": duration_seconds,
    }


def _fetch_transcript_sync(video_id: str) -> tuple[str, str]:
    """Chiamata sincrona (la libreria non è async): va eseguita in threadpool dal chiamante.

    NB: dalla v1.x di youtube-transcript-api l'API è cambiata da classmethod
    (YouTubeTranscriptApi.list_transcripts(...)) a istanza (YouTubeTranscriptApi().list(...)).
    """
    ytt_api = YouTubeTranscriptApi()
    try:
        transcript_list = ytt_api.list(video_id)
    except TranscriptsDisabled:
        raise HTTPException(status_code=422, detail="I sottotitoli sono disabilitati per questo video: impossibile generare gli esercizi.")
    except VideoUnavailable:
        raise HTTPException(status_code=404, detail="Video non disponibile.")

    # Preferisci una trascrizione in inglese (manuale o auto-generata); se non esiste, errore chiaro.
    try:
        transcript = transcript_list.find_transcript(["en", "en-US", "en-GB"])
    except NoTranscriptFound:
        raise HTTPException(
            status_code=422,
            detail="Non è stata trovata una trascrizione in inglese per questo video: assicurati che il video sia in lingua inglese.",
        )

    language_code = transcript.language_code
    fetched = transcript.fetch()  # FetchedTranscript: iterabile di FetchedTranscriptSnippet(text, start, duration)
    full_text = " ".join(snippet.text for snippet in fetched)
    return full_text, language_code


async def validate_and_fetch(url: str) -> ValidatedVideo:
    """Orchestratore: validazione completa del video + recupero trascrizione."""
    from fastapi.concurrency import run_in_threadpool

    video_id = extract_video_id(url)

    metadata = await _fetch_video_metadata(video_id)

    if metadata["duration_seconds"] > MAX_DURATION_SECONDS:
        minutes = metadata["duration_seconds"] // 60
        raise HTTPException(
            status_code=422,
            detail=f"Il video dura circa {minutes} minuti: la durata massima consentita è 10 minuti.",
        )
    if metadata["duration_seconds"] == 0:
        raise HTTPException(status_code=422, detail="Impossibile determinare la durata del video.")

    transcript_text, language_code = await run_in_threadpool(_fetch_transcript_sync, video_id)

    if not language_code.lower().startswith("en"):
        raise HTTPException(
            status_code=422,
            detail=f"Il video sembra essere in lingua '{language_code}', non in inglese.",
        )

    if len(transcript_text.split()) < 40:
        raise HTTPException(
            status_code=422,
            detail="La trascrizione disponibile è troppo breve per generare esercizi significativi.",
        )

    return ValidatedVideo(
        id=video_id,
        title=metadata["title"],
        channel=metadata["channel"],
        duration_seconds=metadata["duration_seconds"],
        transcript_text=transcript_text,
        transcript_language=language_code,
    )
