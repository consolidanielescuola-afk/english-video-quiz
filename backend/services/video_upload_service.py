"""
Percorso alternativo a youtube_service.py: invece di prendere un URL YouTube,
l'insegnante carica direttamente un file video dal proprio computer.

Perché questo percorso esiste: youtube-transcript-api (usato da youtube_service.py)
dipende da uno scraping non ufficiale delle pagine di YouTube, che sugli hosting
cloud gratuiti viene spesso rate-limitato o bloccato (vedi commenti in
youtube_service.py). Caricando il video direttamente, l'app non contatta più
YouTube per niente: usiamo Gemini stesso (già usato per generare gli esercizi,
già gratuito) anche per "guardare" il video e trascriverlo. Nessuna dipendenza
in più da servizi terzi non ufficiali.

Il file video NON viene salvato in modo permanente: viene scritto in un file
temporaneo solo il tempo necessario per caricarlo su Gemini, poi cancellato.
"""

import os
import tempfile
import time
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile
from google import genai

MAX_DURATION_SECONDS = 10 * 60  # stesso limite del percorso YouTube
MAX_FILE_SIZE_BYTES = 150 * 1024 * 1024  # 150 MB: generoso per un video di massimo 10 minuti, ma limita l'uso di banda/memoria sul piano gratuito di Render
FILE_PROCESSING_TIMEOUT_SECONDS = 90  # Gemini elabora il video caricato in modo asincrono prima di poterlo usare

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


@dataclass
class ValidatedUploadedVideo:
    title: str
    duration_seconds: int
    transcript_text: str


def _get_duration_seconds(path: str) -> int:
    """Prova a leggere la durata dal file; se non riesce (formato non supportato da
    mutagen, file corrotto, ecc.) ritorna 0 e si lascia che sia Gemini stesso, più avanti,
    a segnalare eventuali problemi con il file — non blocchiamo l'utente per questo.
    """
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path)
        if audio is not None and audio.info is not None and audio.info.length:
            return int(audio.info.length)
    except Exception:
        pass
    return 0


def _upload_and_transcribe_sync(path: str, mime_type: str) -> str:
    """Chiamata sincrona (l'SDK non è async): va eseguita in threadpool dal chiamante."""
    uploaded = _client.files.upload(file=path, config={"mime_type": mime_type})

    # L'elaborazione del file su Gemini è asincrona: bisogna attendere che passi
    # dallo stato PROCESSING ad ACTIVE prima di poterlo usare in una richiesta.
    waited = 0
    while uploaded.state is not None and uploaded.state.name == "PROCESSING":
        if waited >= FILE_PROCESSING_TIMEOUT_SECONDS:
            raise HTTPException(
                status_code=504,
                detail="Il video sta impiegando troppo tempo a essere elaborato. Riprova con un file più piccolo o più corto.",
            )
        time.sleep(3)
        waited += 3
        uploaded = _client.files.get(name=uploaded.name)

    if uploaded.state is not None and uploaded.state.name == "FAILED":
        raise HTTPException(
            status_code=422,
            detail="Non è stato possibile elaborare il file video: assicurati che sia un video valido (mp4, mov, webm, ...).",
        )

    prompt = (
        "Trascrivi integralmente in inglese tutto il parlato presente in questo video, "
        "parola per parola, senza riassumere e senza aggiungere commenti. "
        "Se il parlato NON è in inglese, rispondi ESATTAMENTE con la sola parola: NONENGLISH "
        "(senza altro testo). Se non c'è alcun parlato comprensibile, rispondi ESATTAMENTE con "
        "la sola parola: NOSPEECH."
    )

    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[uploaded, prompt],
    )
    return (response.text or "").strip()


async def validate_and_transcribe(video: UploadFile) -> ValidatedUploadedVideo:
    """Orchestratore: validazione + trascrizione di un video caricato dall'insegnante."""
    from fastapi.concurrency import run_in_threadpool

    content_type = video.content_type or "video/mp4"
    if not content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Il file caricato non sembra essere un video.")

    suffix = os.path.splitext(video.filename or "")[1] or ".mp4"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
            total_size = 0
            while chunk := await video.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Il file è troppo grande: il limite è 150 MB. Prova a comprimere il video o a ridurne la durata.",
                    )
                tmp.write(chunk)

        if total_size == 0:
            raise HTTPException(status_code=400, detail="Il file caricato è vuoto.")

        duration_seconds = _get_duration_seconds(tmp_path)
        if duration_seconds > MAX_DURATION_SECONDS:
            minutes = duration_seconds // 60
            raise HTTPException(
                status_code=422,
                detail=f"Il video dura circa {minutes} minuti: la durata massima consentita è 10 minuti.",
            )

        transcript_text = await run_in_threadpool(_upload_and_transcribe_sync, tmp_path, content_type)

        if transcript_text == "NONENGLISH":
            raise HTTPException(
                status_code=422,
                detail="Il parlato nel video non sembra essere in inglese.",
            )
        if transcript_text == "NOSPEECH" or len(transcript_text.split()) < 40:
            raise HTTPException(
                status_code=422,
                detail="Non è stato rilevato un parlato sufficiente nel video per generare esercizi significativi.",
            )

        title = os.path.splitext(video.filename or "")[0].strip() or "Video caricato"
        return ValidatedUploadedVideo(
            title=title,
            duration_seconds=duration_seconds,
            transcript_text=transcript_text,
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
