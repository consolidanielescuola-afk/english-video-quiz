"""
Entrypoint FastAPI.

Endpoint:
  POST /api/generate-from-file  -> a partire da un file video caricato dall'insegnante,
                                    genera la scheda completa (Worksheet) come JSON: la
                                    trascrizione viene prodotta da Gemini stesso guardando
                                    il video (nessuna dipendenza da servizi esterni come
                                    YouTube). Il file viene cancellato subito dopo l'uso,
                                    non viene mai salvato in modo permanente sul server.
  POST /api/export-pdf          -> renderizza la scheda ricevuta in PDF (Playwright) e la
                                    restituisce come file scaricabile (versione studente o,
                                    con include_answers=true, versione con le soluzioni).

Esegui con:  uvicorn main:app --reload --port 8000
"""

import os
import uuid

from dotenv import load_dotenv

load_dotenv()  # carica .env prima di importare i servizi che leggono le API key

import json

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from models.schemas import (
    CEFRLevel,
    EXERCISE_COUNT_MAX,
    ExerciseType,
    ExportPdfRequest,
    VideoInfo,
    Worksheet,
)
from services import ai_generator, pdf_service, video_upload_service

app = FastAPI(title="EnglishQuiz da video caricato")

# In locale (nessuna FRONTEND_ORIGIN impostata) accetta qualsiasi origine.
# In produzione impostare FRONTEND_ORIGIN sull'URL esatto del frontend (es. Netlify)
# per non lasciare l'API aperta a tutti.
_frontend_origin = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _parse_exercise_counts(raw: str) -> dict[ExerciseType, int]:
    """Valida il JSON '{"multiple_choice": 3, "true_false": 2, ...}' inviato dal form."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="exercise_counts non valido.")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="exercise_counts deve essere un oggetto.")

    counts: dict[ExerciseType, int] = {}
    for key, value in parsed.items():
        try:
            ex_type = ExerciseType(key)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Tipologia di esercizio sconosciuta: {key}.")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise HTTPException(status_code=400, detail=f"Numero di esercizi non valido per {key}.")
        if value > EXERCISE_COUNT_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"Massimo {EXERCISE_COUNT_MAX} esercizi per tipologia (richiesti {value} per {key}).",
            )
        counts[ex_type] = value

    if sum(counts.values()) == 0:
        raise HTTPException(status_code=400, detail="Scegli almeno un esercizio in una tipologia.")

    return counts


@app.post("/api/generate-from-file", response_model=Worksheet)
async def generate_worksheet_from_file(
    level: CEFRLevel = Form(...),
    exercise_counts: str = Form(...),  # JSON: {"multiple_choice": 3, "true_false": 2, ...}
    video: UploadFile = File(...),
):
    counts = _parse_exercise_counts(exercise_counts)

    # 1. Validazione file + trascrizione via Gemini (il video non viene salvato: cancellato
    #    subito dopo l'upload verso Gemini, vedi video_upload_service.py)
    validated = await video_upload_service.validate_and_transcribe(video)

    # 2. Generazione esercizi via LLM, nel numero richiesto per ciascuna tipologia
    exercises = await ai_generator.generate_exercises(
        transcript=validated.transcript_text,
        level=level,
        exercise_counts=counts,
    )

    # Garantisce id univoci anche se l'LLM ne generasse di duplicati per errore
    seen_ids = set()
    for ex in exercises:
        if ex.id in seen_ids:
            ex.id = f"{ex.id}-{uuid.uuid4().hex[:4]}"
        seen_ids.add(ex.id)

    return Worksheet(
        video=VideoInfo(
            title=validated.title,
            duration_seconds=validated.duration_seconds,
        ),
        level=level,
        exercises=exercises,
    )


@app.post("/api/export-pdf")
async def export_pdf(payload: ExportPdfRequest):
    try:
        pdf_bytes = await pdf_service.render_worksheet_pdf(
            worksheet=payload.worksheet,
            include_answers=payload.include_answers,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore nella generazione del PDF: {e}")

    suffix = "-soluzioni" if payload.include_answers else ""
    filename = f"{payload.worksheet.video.title[:40]}{suffix}.pdf".replace("/", "-")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
