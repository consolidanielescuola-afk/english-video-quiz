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
     naturale + un esempio, e che chiede un numero ESATTO di esercizi per
     ciascuna tipologia (scelto dall'insegnante nel form). In più, forziamo
     il formato JSON anche a livello di API con response_mime_type="application/json".
  2. Parsing del testo di risposta -> se non è JSON valido, non rispetta lo
     schema Pydantic (Exercise discriminated union) oppure il numero di
     esercizi per tipo non corrisponde a quanto richiesto, un retry con un
     messaggio di correzione che include l'errore.
  3. Se anche il retry non produce un conteggio perfetto ma il JSON è
     comunque valido, si restituisce il miglior risultato ottenuto piuttosto
     che far fallire l'intera generazione. Solo se nessun tentativo produce
     JSON valido si propaga un 502 controllato.
"""

import json
import os
from typing import Dict, List

from fastapi import HTTPException
from google import genai
from google.genai import types
from pydantic import TypeAdapter, ValidationError

from models.schemas import CEFRLevel, Exercise, ExerciseType

# NB: verifica sempre l'elenco aggiornato dei modelli gratuiti su
# https://ai.google.dev/gemini-api/docs/pricing (i modelli "Flash" sono quelli gratuiti)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
MAX_TRANSCRIPT_CHARS = 12_000  # tronca trascrizioni molto lunghe per contenere costi/latenza

_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

TYPE_LABELS = {
    ExerciseType.MULTIPLE_CHOICE: "multiple choice",
    ExerciseType.TRUE_FALSE: "true/false",
    ExerciseType.GAP_FILL: "gap fill",
    ExerciseType.MATCHING: "matching",
    ExerciseType.OPEN_ENDED: "open ended",
}

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
        "(coppie parola/definizione o inizio/fine frase, basate sul vocabolario del video: il numero di "
        "coppie in 'pairs' deve essere uguale al numero di esercizi 'matching' richiesto)"
    ),
    ExerciseType.OPEN_ENDED: (
        'open_ended: {"type":"open_ended","id":"oe1","question":"...","model_answer":"..."} '
        "(domande di comprensione/opinione che richiedono una risposta discorsiva; "
        "model_answer è una risposta di riferimento plausibile, non l'unica corretta)"
    ),
}


def build_prompt(transcript: str, level: CEFRLevel, exercise_counts: Dict[ExerciseType, int]) -> str:
    truncated = transcript[:MAX_TRANSCRIPT_CHARS]
    active_counts = {t: c for t, c in exercise_counts.items() if c > 0}
    total = sum(active_counts.values())
    types_block = "\n".join(
        f"- ESATTAMENTE {count} esercizi di tipo {TYPE_INSTRUCTIONS[t]}"
        for t, count in active_counts.items()
    )

    return f"""Sei un insegnante di inglese esperto nella creazione di materiale didattico secondo il Quadro Comune Europeo di Riferimento (CEFR).

Ecco la trascrizione di un video in inglese:
\"\"\"
{truncated}
\"\"\"

Genera esercizi di comprensione basati ESCLUSIVAMENTE sui contenuti di questa trascrizione, calibrati per il livello CEFR {level.value}:
- il lessico e la complessità sintattica delle domande e delle opzioni devono essere adatti al livello {level.value}
- per i livelli A1/A2: frasi semplici, vocabolario di base, domande dirette su fatti espliciti
- per i livelli B1/B2: inferenze, vocabolario più ampio, alcune domande su opinioni/dettagli impliciti
- per il livello C1: sfumature, lessico avanzato, inferenze complesse

Genera il seguente numero ESATTO di esercizi per ciascuna tipologia (totale {total} esercizi):
{types_block}

Rispondi ESCLUSIVAMENTE con un array JSON valido (nessun testo prima o dopo, nessun blocco markdown ```), dove ogni elemento è un oggetto esercizio conforme esattamente a uno degli schemi sopra descritti. L'array deve contenere esattamente {total} elementi in totale, con il numero richiesto per ciascuna tipologia. Gli "id" devono essere univoci nell'intero array."""


def _extract_json_array(raw_text: str) -> str:
    """Rimuove eventuali fence markdown (```json ... ```) che l'LLM potrebbe comunque aggiungere."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _count_by_type(exercises: List[Exercise]) -> Dict[ExerciseType, int]:
    counts: Dict[ExerciseType, int] = {}
    for ex in exercises:
        ex_type = ExerciseType(ex.type)
        counts[ex_type] = counts.get(ex_type, 0) + 1
    return counts


def _counts_match(exercises: List[Exercise], expected: Dict[ExerciseType, int]) -> bool:
    expected_active = {t: c for t, c in expected.items() if c > 0}
    return _count_by_type(exercises) == expected_active


def _call_llm(prompt: str, correction_note: str | None, max_output_tokens: int) -> str:
    contents = prompt
    if correction_note:
        contents = f"{prompt}\n\n---\n(La tua risposta precedente non era valida)\n{correction_note}"

    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.7,
            max_output_tokens=max_output_tokens,
        ),
    )
    return response.text


async def generate_exercises(
    transcript: str, level: CEFRLevel, exercise_counts: Dict[ExerciseType, int]
) -> List[Exercise]:
    from fastapi.concurrency import run_in_threadpool

    prompt = build_prompt(transcript, level, exercise_counts)
    exercise_list_adapter = TypeAdapter(List[Exercise])

    total = sum(c for c in exercise_counts.values() if c > 0)
    # ~500 token per esercizio è una stima generosa; teniamoci in un range ragionevole.
    max_output_tokens = min(8192, max(2048, total * 500))

    last_error: Exception | str | None = None
    fallback_exercises: List[Exercise] | None = None

    for attempt in range(2):  # 1 tentativo + 1 retry di correzione
        correction_note = None
        if last_error is not None:
            correction_note = (
                f"Il tentativo precedente non era corretto: {last_error}. "
                "Rispondi di nuovo con SOLO l'array JSON corretto, senza testo aggiuntivo, "
                "rispettando esattamente il numero di esercizi richiesto per ciascuna tipologia."
            )

        raw_text = await run_in_threadpool(_call_llm, prompt, correction_note, max_output_tokens)

        try:
            json_str = _extract_json_array(raw_text)
            parsed = json.loads(json_str)
            exercises = exercise_list_adapter.validate_python(parsed)
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = e
            continue

        if _counts_match(exercises, exercise_counts):
            return exercises

        # JSON valido ma conteggio sbagliato: teniamolo come rete di sicurezza e proviamo
        # comunque a correggere con un secondo tentativo.
        fallback_exercises = exercises
        last_error = (
            f"numero di esercizi per tipologia non corretto (attesi: "
            f"{ {TYPE_LABELS[t]: c for t, c in exercise_counts.items() if c > 0} }, "
            f"ricevuti: {_count_by_type(exercises)})"
        )

    if fallback_exercises is not None:
        return fallback_exercises

    raise HTTPException(
        status_code=502,
        detail="Il modello AI non ha restituito esercizi in un formato valido. Riprova.",
    ) from (last_error if isinstance(last_error, Exception) else None)
