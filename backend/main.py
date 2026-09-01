"""
Entrypoint FastAPI.

Endpoint:
  POST /api/generate     -> valida il video, recupera la trascrizione, chiama l'LLM,
                             restituisce la scheda completa (Worksheet) come JSON.
  POST /api/export-pdf   -> renderizza la scheda ricevuta in PDF (Playwright) e la
                             restituisce come file scaricabile.

Esegui con:  uvicorn main:app --reload --port 8000
"""

import os
import uuid

from dotenv import load_dotenv

load_dotenv()  # carica .env prima di importare i servizi che leggono le API key

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from models.schemas import ExportPdfRequest, GenerateRequest, VideoInfo, Worksheet
from services import ai_generator, pdf_service, youtube_service

app = FastAPI(title="EnglishQuiz from YouTube API")

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


@app.post("/api/generate", response_model=Worksheet)
async def generate_worksheet(payload: GenerateRequest):
    # 1. Validazione video (esistenza, lingua, durata) + trascrizione
    validated = await youtube_service.validate_and_fetch(payload.youtube_url)

    # 2. Generazione esercizi via LLM, già validati contro lo schema Pydantic
    exercises = await ai_generator.generate_exercises(
        transcript=validated.transcript_text,
        level=payload.level,
        exercise_types=payload.exercise_types,
    )

    # Garantisce id univoci anche se l'LLM ne generasse di duplicati per errore
    seen_ids = set()
    for ex in exercises:
        if ex.id in seen_ids:
            ex.id = f"{ex.id}-{uuid.uuid4().hex[:4]}"
        seen_ids.add(ex.id)

    return Worksheet(
        video=VideoInfo(
            id=validated.id,
            title=validated.title,
            channel=validated.channel,
            duration_seconds=validated.duration_seconds,
        ),
        level=payload.level,
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

    filename = f"{payload.worksheet.video.title[:40]}.pdf".replace("/", "-")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
