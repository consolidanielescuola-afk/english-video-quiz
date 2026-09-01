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
import threading
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import HTTPException
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api._errors import IpBlocked, RequestBlocked
from youtube_transcript_api.proxies import WebshareProxyConfig

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
MAX_DURATION_SECONDS = 10 * 60  # requisito di progetto: max 10 minuti

# YouTube blocca le richieste che arrivano dagli IP dei provider cloud
# (Render/AWS/GCP/Azure/...): senza un proxy, ytt_api.list()/.fetch() falliscono
# con IpBlocked/RequestBlocked non appena l'app gira su un hosting "vero".
# Webshare offre 10 proxy datacenter gratuiti (nessuna carta richiesta): se le
# credenziali sono configurate le usiamo, altrimenti si prova senza proxy
# (va benissimo in locale, dove l'IP di casa non è bloccato).
#
# NB: restando sul piano gratuito (scelta esplicita dell'utente per mantenere
# il progetto a costo zero), questi proxy datacenter condivisi vengono comunque
# rate-limitati da YouTube (429) in modo non deterministico: non c'è modo di
# eliminare del tutto il rischio di fallimento senza passare a un proxy
# residenziale a pagamento. Per massimizzare l'affidabilità restando gratis
# si fanno più tentativi (vedi _fetch_transcript_sync) e si mette in cache il
# risultato per video, così una classe intera che usa lo stesso video paga il
# "costo" del primo tentativo una sola volta.
WEBSHARE_PROXY_USERNAME = os.getenv("WEBSHARE_PROXY_USERNAME")
WEBSHARE_PROXY_PASSWORD = os.getenv("WEBSHARE_PROXY_PASSWORD")


def _build_ytt_api(use_proxy: bool) -> YouTubeTranscriptApi:
    if use_proxy and WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD:
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=WEBSHARE_PROXY_USERNAME,
                proxy_password=WEBSHARE_PROXY_PASSWORD,
            )
        )
    return YouTubeTranscriptApi()


# Cache in memoria (per-processo, si svuota a ogni riavvio del servizio): evita
# di richiamare YouTube per lo stesso video più volte nello stesso periodo,
# utile perché più studenti della stessa classe generalmente usano lo stesso
# video. Non serve altro (Redis, DB...) per un uso didattico non massivo.
_TRANSCRIPT_CACHE: dict[str, tuple[float, str, str]] = {}
_TRANSCRIPT_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 ore
_transcript_cache_lock = threading.Lock()


def _cache_get_transcript(video_id: str) -> Optional[tuple[str, str]]:
    with _transcript_cache_lock:
        entry = _TRANSCRIPT_CACHE.get(video_id)
    if not entry:
        return None
    cached_at, text, lang = entry
    if time.time() - cached_at > _TRANSCRIPT_CACHE_TTL_SECONDS:
        return None
    return text, lang


def _cache_set_transcript(video_id: str, text: str, lang: str) -> None:
    with _transcript_cache_lock:
        _TRANSCRIPT_CACHE[video_id] = (time.time(), text, lang)


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


def _fetch_transcript_attempt(video_id: str, use_proxy: bool) -> tuple[str, str]:
    """Un singolo tentativo di recupero trascrizione, con o senza proxy.

    NB: dalla v1.x di youtube-transcript-api l'API è cambiata da classmethod
    (YouTubeTranscriptApi.list_transcripts(...)) a istanza (YouTubeTranscriptApi().list(...)).
    Può sollevare TranscriptsDisabled / VideoUnavailable / NoTranscriptFound
    (errori "definitivi", non ha senso ritentare) oppure IpBlocked / RequestBlocked
    (errori "temporanei" dovuti al blocco IP/rate-limit di YouTube: qui ha senso
    ritentare, eventualmente cambiando percorso di rete).
    """
    ytt_api = _build_ytt_api(use_proxy)
    transcript_list = ytt_api.list(video_id)
    transcript = transcript_list.find_transcript(["en", "en-US", "en-GB"])
    language_code = transcript.language_code
    fetched = transcript.fetch()  # FetchedTranscript: iterabile di FetchedTranscriptSnippet(text, start, duration)
    full_text = " ".join(snippet.text for snippet in fetched)
    return full_text, language_code


def _fetch_transcript_sync(video_id: str) -> tuple[str, str]:
    """Chiamata sincrona (la libreria non è async): va eseguita in threadpool dal chiamante.

    Restando sul piano gratuito di Webshare, i proxy datacenter condivisi vengono
    ogni tanto rate-limitati da YouTube in modo non prevedibile: per massimizzare le
    probabilità di successo si tenta più volte, e si evita del tutto una nuova
    richiesta a YouTube se il video è già stato processato di recente (cache).
    """
    cached = _cache_get_transcript(video_id)
    if cached:
        return cached

    # Ordine dei tentativi: due volte con il proxy Webshare (se configurato: ogni
    # tentativo apre una nuova connessione e può quindi capitare su un IP diverso
    # del pool), poi un ultimo tentativo diretto senza proxy (a volte l'IP del
    # server non è, in quel momento, tra quelli bloccati).
    attempts = [True, True, False] if (WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD) else [False]

    for attempt_index, use_proxy in enumerate(attempts):
        try:
            text, lang = _fetch_transcript_attempt(video_id, use_proxy)
            _cache_set_transcript(video_id, text, lang)
            return text, lang
        except TranscriptsDisabled:
            raise HTTPException(status_code=422, detail="I sottotitoli sono disabilitati per questo video: impossibile generare gli esercizi.")
        except VideoUnavailable:
            raise HTTPException(status_code=404, detail="Video non disponibile.")
        except NoTranscriptFound:
            raise HTTPException(
                status_code=422,
                detail="Non è stata trovata una trascrizione in inglese per questo video: assicurati che il video sia in lingua inglese.",
            )
        except (IpBlocked, RequestBlocked):
            if attempt_index < len(attempts) - 1:
                continue  # errore temporaneo: prova il tentativo successivo
            raise HTTPException(
                status_code=503,
                detail=(
                    "YouTube sta limitando temporaneamente le richieste al servizio (succede con i proxy "
                    "gratuiti). Riprova tra qualche minuto: spesso il tentativo successivo va a buon fine."
                ),
            )


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
