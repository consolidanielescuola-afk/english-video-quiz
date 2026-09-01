"""
Costruzione del prompt e chiamata all'LLM (Google Gemini) per generare gli
esercizi a partire dalla trascrizione del video.

Perché Gemini: a differenza di Claude/OpenAI, la Gemini API ha un piano
gratuito reale sui modelli "Flash" (nessuna carta di credito richiesta per
iniziare) — scelta adatta a un progetto didattico a basso volume. Se in
futuro serve più qualità o quota, si può tornare a Claude/OpenAI toccando
solo questo file (le funzioni build_prompt/generate_exercises restano
invariate, cambia solo _call_llm).

Strategia:
  1. Prompt che impone ESPLICITAMENTE di rispondere con solo JSON (nessun
     markdown, nessun commento), conforme allo schema descritto in linguaggio
     naturale + un esempio. In più, forziamo il formato JSON anche a livello
     di API con response_mime_type="application/json".
  2. Parsing del testo di risposta -> se non è JSON valido o non rispetta lo
     schema Pydantic (Exercise discriminated union), un retry con un messaggio
     di correzione che include l'errore di validazione.
  3. Se il retry fallisce ancora, propaghiamo un 502 controllato invece di
     restituire dati sporchi al frontend.
"""

import json
import os
from typing import List

from fastapi import HTTPException
from google import genai
from google.genai import types
from pydantic import TypeAdapter, ValidationError

from models.schemas import CEFRLevel, Exercise, ExerciseType

# NB: verifica sempre l'elenco aggiornato dei modelli gratuiti su
# https://ai.google.dev/gemini-api/docs/pricing (i modelli "Flash" sono quelli gratuiti)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_TRANSCRIPT_CHARS = 12_000  # tronca trascrizioni molto lunghe per contenere costi/latenza

_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

EXERCISES_PER_TYPE = 3

TYPE_INSTRUCTIONS = {
    ExerciseType.MULTIPLE_CHOICE: (
        'multiple_choice: {"type":"multiple_choice","id":"mc1","question":"...",'
        '"options":["...","...","...","..."],"correct_index":0} '
        "(4 opzioni, un solo indice corretto, distrattori plausibili basati sul contenuto del video)"
    ),
    ExerciseType.TRUE_FALSE: (
        'true_false: {"type":"true_false","id":"tf1","statement":"...","correct":true} '
        "(affermazioni basate su fatti espliciti o impliciti nel video)"
    ),
    ExerciseType.GAP_FILL: (
        'gap_fill: {"type":"gap_fill","id":"gf1","text":"I ___ to school every day.",'
        '"blanks":[{"answers":["go"]}]} '
        "(usa esattamente '___' nel punto di ogni spazio vuoto, nello stesso ordine dell'array blanks; "
        "in 'answers' includi eventuali forme alternative accettabili, es. contrazioni)"
    ),
    ExerciseType.MATCHING: (
        'matching: {"type":"matching","id":"m1","pairs":[{"left":"word","right":"definition"}, ...]} '
        "(4-5 coppie parola/definizione o inizio/fine frase, basate sul vocabolario del video)"
    ),
    ExerciseType.OPEN_ENDED: (
        'open_ended: {"type":"open_ended","id":"oe1","question":"...","model_answer":"..."} '
        "(domande di comprensione/opinione che richiedono una risposta discorsiva; "
        "model_answer è una risposta di riferimento plausibile, non l'unica corretta)"
    ),
}


def build_prompt(transcript: str, level: CEFRLevel, exercise_types: List[ExerciseType]) -> str:
    truncated = transcript[:MAX_TRANSCRIPT_CHARS]
    types_block = "\n".join(f"- {TYPE_INSTRUCTIONS[t]}" for t in exercise_types)

    return f"""Sei un insegnante di inglese esperto nella creazione di materiale didattico secondo il Quadro Comune Europeo di Riferimento (CEFR).

Ecco la trascrizione di un video YouTube in inglese:
\"\"\"
{truncated}
\"\"\"

Genera esercizi di comprensione basati ESCLUSIVAMENTE sui contenuti di questa trascrizione, calibrati per il livello CEFR {level.value}:
- il lessico e la complessità sintattica delle domande e delle opzioni devono essere adatti al livello {level.value}
- per i livelli A1/A2: frasi semplici, vocabolario di base, domande dirette su fatti espliciti
- per i livelli B1/B2: inferenze, vocabolario più ampio, alcune domande su opinioni/dettagli impliciti
- per il livello C1: sfumature, lessico avanzato, inferenze complesse

Genera {EXERCISES_PER_TYPE} esercizi per ciascuna delle seguenti tipologie:
{types_block}

Rispondi ESCLUSIVAMENTE con un array JSON valido (nessun testo prima o dopo, nessun blocco markdown ```), dove ogni elemento è un oggetto esercizio conforme esattamente a uno degli schemi sopra descritti. Gli "id" devono essere univoci nell'intero array."""


def _extract_json_array(raw_text: str) -> str:
    """Rimuove eventuali fence markdown (```json ... ```) che l'LLM potrebbe comunque aggiungere."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _call_llm(prompt: str, correction_note: str | None = None) -> str:
    contents = prompt
    if correction_note:
        contents = f"{prompt}\n\n---\n(La tua risposta precedente non era valida)\n{correction_note}"

    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.7,
            max_output_tokens=4096,
        ),
    )
    return response.text


async def generate_exercises(
    transcript: str, level: CEFRLevel, exercise_types: List[ExerciseType]
) -> List[Exercise]:
    from fastapi.concurrency import run_in_threadpool

    prompt = build_prompt(transcript, level, exercise_types)
    exercise_list_adapter = TypeAdapter(List[Exercise])

    last_error: Exception | None = None
    for attempt in range(2):  # 1 tentativo + 1 retry di correzione
        correction_note = None
        if last_error is not None:
            correction_note = (
                f"Il JSON che hai restituito non era valido: {last_error}. "
                "Rispondi di nuovo con SOLO l'array JSON corretto, senza testo aggiuntivo."
            )

        raw_text = await run_in_threadpool(_call_llm, prompt, correction_note)

        try:
            json_str = _extract_json_array(raw_text)
            parsed = json.loads(json_str)
            exercises = exercise_list_adapter.validate_python(parsed)
            return exercises
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = e
            continue

    raise HTTPException(
        status_code=502,
        detail="Il modello AI non ha restituito esercizi in un formato valido. Riprova.",
    ) from last_error
