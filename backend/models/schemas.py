"""
Schemi Pydantic condivisi da tutti gli endpoint.

L'uso di una discriminated union su "type" garantisce che l'output dell'LLM
(parsato in ai_generator.py) sia validato automaticamente contro la forma
esatta attesa da ciascuna tipologia di esercizio: se il modello genera JSON
malformato o incompleto, Pydantic solleva un errore che possiamo intercettare
e trasformare in un retry (vedi ai_generator.generate_exercises).
"""

from enum import Enum
from typing import Annotated, List, Literal, Union

from pydantic import BaseModel, Field


class CEFRLevel(str, Enum):
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"


class ExerciseType(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    GAP_FILL = "gap_fill"
    MATCHING = "matching"
    OPEN_ENDED = "open_ended"


# Numero massimo di esercizi selezionabili per ciascuna tipologia (lato form
# "quante domande per tipo"): limita l'output dell'LLM a una dimensione
# ragionevole e prevedibile.
EXERCISE_COUNT_MAX = 10


# ---------------------------------------------------------------------------
# Esercizi (discriminated union su "type")
# ---------------------------------------------------------------------------

class MultipleChoiceExercise(BaseModel):
    type: Literal["multiple_choice"] = "multiple_choice"
    id: str
    question: str
    options: List[str] = Field(min_length=3, max_length=5)
    correct_index: int


class TrueFalseExercise(BaseModel):
    type: Literal["true_false"] = "true_false"
    id: str
    statement: str
    correct: bool


class GapBlank(BaseModel):
    # più forme accettabili come corrette (es. sinonimi, contrazioni: "don't"/"do not")
    answers: List[str] = Field(min_length=1)


class GapFillExercise(BaseModel):
    type: Literal["gap_fill"] = "gap_fill"
    id: str
    # il testo contiene "___" nel punto esatto di ogni blank, nell'ordine di "blanks"
    text: str
    blanks: List[GapBlank] = Field(min_length=1)


class MatchingPair(BaseModel):
    left: str
    right: str


class MatchingExercise(BaseModel):
    type: Literal["matching"] = "matching"
    id: str
    pairs: List[MatchingPair] = Field(min_length=3, max_length=6)


class OpenEndedExercise(BaseModel):
    type: Literal["open_ended"] = "open_ended"
    id: str
    question: str
    model_answer: str  # risposta di riferimento per l'auto-valutazione, non usata per il punteggio


Exercise = Annotated[
    Union[
        MultipleChoiceExercise,
        TrueFalseExercise,
        GapFillExercise,
        MatchingExercise,
        OpenEndedExercise,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Video + Scheda completa
# ---------------------------------------------------------------------------

class VideoInfo(BaseModel):
    # Il video è sempre caricato dall'insegnante (nessun link/embed esterno):
    # solo titolo (nome del file) e durata sono rilevanti per la scheda.
    title: str
    duration_seconds: int


class Worksheet(BaseModel):
    video: VideoInfo
    level: CEFRLevel
    exercises: List[Exercise]


class ExportPdfRequest(BaseModel):
    worksheet: Worksheet
    include_answers: bool = False
