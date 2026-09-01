# EnglishQuiz — Architettura e Guida al Progetto

Web app che genera schede didattiche di inglese interattive (video + esercizi) a partire da un **video caricato direttamente dall'insegnante**, con livello CEFR, numero di domande scelto per ciascuna tipologia di esercizio, correzione istantanea (con soluzione visualizzabile a richiesta) ed export PDF (versione studente e versione con soluzioni).

---

## 1. Architettura e Tech Stack

### Vista d'insieme

```
┌──────────────┐   multipart/form-data   ┌───────────────────┐      ┌──────────────────┐
│   Frontend   │ ───────────────────────▶│   Backend FastAPI   │ ───▶│  Google Gemini API │
│ HTML/CSS/JS  │ ◀────────────────────── │  (Python, async)    │ ◀───│  Files API         │
│  (Vanilla +  │      JSON (Worksheet)   │                      │     │  (trascrizione)    │
│  Tailwind)   │                         └─────────┬────────────┘     │  + generazione      │
└──────────────┘                                   │                  │  esercizi (JSON)    │
       ▲                                            ▼                  └──────────────────┘
       │                                  ┌───────────────────┐
       └── video riprodotto localmente ── │  Playwright (PDF)   │
           (URL.createObjectURL, mai      │  studente/soluzioni │
           ricaricato sul server)         └───────────────────┘
```

Il video **non transita mai da nessuna parte se non dal browser dell'insegnante al server (una volta sola, per l'analisi) e da Gemini**: non viene salvato in modo permanente né incorporato nella scheda via link esterno.

### Perché queste scelte

**Frontend: Vanilla JS + Tailwind CSS (via CDN), niente build step**
Per un progetto di questa dimensione (un form di input + una vista "scheda" dinamica), React aggiunge complessità di build/tooling senza un reale bisogno di componenti riutilizzabili complessi o routing. Vanilla JS con Tailwind è più veloce da avviare, facile da capire e da deployare come file statici. Se in futuro la app cresce (gestione utenti, storico schede, dashboard insegnante), consiglio di migrare a **React + Vite** mantenendo la stessa API backend.

**Backend: FastAPI (Python)**
- Async nativo → utile perché le chiamate all'LLM sono I/O-bound e vanno gestite con timeout puliti.
- Validazione automatica request/response con **Pydantic** → garantisce che l'output dell'LLM rispetti uno schema JSON rigoroso (tipi di esercizio, struttura domande/risposte).
- Documentazione OpenAPI automatica (`/docs`).

**Upload diretto del video + trascrizione: Gemini Files API (`google-genai`)**
- `client.files.upload(file=path)` carica il video su Gemini, poi si attende (polling su `client.files.get`) che passi da `PROCESSING` ad `ACTIVE`, infine `client.models.generate_content(model=..., contents=[uploaded_file, prompt])` produce la trascrizione — usando la comprensione multimodale nativa del modello, senza bisogno di sottotitoli o servizi esterni.
- Gemini stesso segnala se il parlato non è in inglese o è assente (risponde con sentinel `NONENGLISH`/`NOSPEECH`), così la validazione riusa la stessa gestione di errore per entrambi i casi.
- Il file video viene scritto in un file temporaneo sul server solo il tempo di caricarlo su Gemini, poi **cancellato immediatamente** (vedi `backend/services/video_upload_service.py`). Per la riproduzione nella scheda, il video resta **esclusivamente nel browser dell'insegnante** (`URL.createObjectURL`) e non torna mai sul server una seconda volta.
- **Nessun limite di durata**: l'unico vincolo è la dimensione del file (default 150 MB, vedi `MAX_FILE_SIZE_BYTES`), che limita indirettamente anche i tempi di upload/elaborazione sul piano gratuito di Render. Il numero massimo di esercizi per tipologia è invece limitato (`EXERCISE_COUNT_MAX`, default 10) per mantenere prevedibili tempi e costo della generazione.
- Endpoint `multipart/form-data` (`POST /api/generate-from-file`, richiede il pacchetto `python-multipart`) invece di JSON, per poter inviare il file binario insieme a livello CEFR e conteggio esercizi per tipologia.

**Generazione esercizi: Google Gemini API (piano gratuito)**
- Si invia la trascrizione (troncata/pulita) + un prompt strutturato che richiede **esclusivamente JSON** conforme a uno schema fisso (vedi `backend/services/ai_generator.py`), rinforzato anche a livello di API con `response_mime_type="application/json"`.
- Il numero di esercizi per ciascuna tipologia è scelto dall'insegnante nel form (0–10 per tipo): il prompt chiede esplicitamente quel numero esatto per tipo, e la risposta viene validata anche sul conteggio (se non corrisponde, si tenta un retry con una nota di correzione; se il retry fallisce ugualmente si restituisce comunque il miglior risultato valido ottenuto, piuttosto che fallire del tutto).
- Scelto al posto di Claude/OpenAI perché la Gemini API mette a disposizione modelli "Flash" gratuiti (nessuna carta di credito richiesta). Il codice isola la chiamata all'LLM in un'unica funzione (`_call_llm`), quindi passare a un altro provider in futuro richiede di toccare solo quella funzione.
- **Nota sui nomi dei modelli**: Google ritira periodicamente i modelli più vecchi (es. `gemini-2.5-flash` è stato dismesso per i nuovi utenti a favore di `gemini-3.6-flash`, il modello usato di default in questo progetto). Se in futuro l'app smette di generare esercizi con un errore "model ... is no longer available", il messaggio di Google indica sempre il nome del modello sostitutivo: basta aggiornare la variabile d'ambiente `GEMINI_MODEL` su Render (Settings → Environment) senza toccare il codice.
- Compromesso da conoscere: i modelli gratuiti "Flash" sono meno raffinati di un modello di punta nel calibrare con precisione i livelli CEFR più alti (C1) o nel generare distrattori molto sottili per le multiple choice — per un uso didattico standard restano comunque adeguati.

**Generazione PDF: Playwright (server-side)**
- La scheda è già una pagina HTML/CSS ben impaginata: la soluzione più robusta è **renderizzarla in un browser headless (Playwright) e stamparla in PDF** (`page.pdf()`), perché rispetta perfettamente CSS, `@media print`, interruzioni di pagina (`page-break-*`).
- L'endpoint `/api/export-pdf` genera due varianti a seconda di `include_answers`: versione studente (nessuna soluzione) o versione con le soluzioni in pagina separata (interruzione di pagina prima dell'answer key). Il frontend offre due pulsanti distinti per scaricarle entrambe.
- **Nota per il deploy su Render (o hosting nativi simili, non-Docker)**: Playwright scarica il browser Chromium in `~/.cache/ms-playwright` per default — una cartella FUORI dalla directory del progetto. Su Render, solo la directory del progetto viene "promossa" dall'ambiente di build a quello di runtime: il browser scaricato durante il build va quindi perso, e al primo utilizzo compare l'errore `BrowserType.launch: Executable doesn't exist`. La soluzione è impostare la variabile d'ambiente `PLAYWRIGHT_BROWSERS_PATH=0`, che dice a Playwright di installare il browser dentro la cartella del pacchetto stesso (quindi dentro il progetto, e quindi promossa correttamente). Va impostata *prima* del deploy — è già in `render.yaml`.

### Tabella riassuntiva

| Livello | Tecnologia | Motivazione |
|---|---|---|
| Frontend | Vanilla JS + Tailwind CSS (CDN) | Zero build, rapido da avviare, facile migrazione futura a React |
| Backend | FastAPI (Python 3.11+) | Async, validazione Pydantic, OpenAPI automatico |
| Trascrizione | Gemini Files API (comprensione multimodale nativa) | Nessuna dipendenza da servizi/scraping esterni, nessun limite di durata |
| Generazione esercizi | Google Gemini API (`gemini-3.6-flash`, gratuito) | Nessun costo per uso didattico non massivo, output JSON vincolato, conteggio per tipo configurabile |
| Correzione | JS lato client | Istantanea, nessuna latenza di rete; soluzione mostrabile a richiesta |
| PDF | Playwright (server) | Fedeltà di stampa massima; versione studente e versione soluzioni |
| Hosting suggerito | Frontend: Netlify/Vercel/Render — Backend: Render/Fly.io/Railway | Deploy gratuito/economico, HTTPS incluso |

---

## 2. Struttura del progetto

```
english-video-quiz/
├── README.md
├── render.yaml
├── frontend/
│   ├── index.html          # pagina unica: form input + vista scheda (SPA leggera)
│   ├── styles.css          # stili custom minimi (oltre Tailwind CDN) + regole @media print
│   └── app.js              # logica: upload, rendering dinamico, correzione, export PDF
│
└── backend/
    ├── main.py              # entrypoint FastAPI, definizione endpoint
    ├── requirements.txt
    ├── .env.example         # GEMINI_API_KEY, GEMINI_MODEL
    ├── models/
    │   └── schemas.py       # Pydantic: Exercise, Worksheet, VideoInfo...
    └── services/
        ├── video_upload_service.py  # validazione file + upload/trascrizione via Gemini Files API
        ├── ai_generator.py          # prompt building + chiamata LLM + parsing/validazione JSON
        └── pdf_service.py           # generazione PDF via Playwright (studente/soluzioni)
```

Convenzione: ogni "servizio" del backend fa una cosa sola ed è testabile in isolamento (si può mockare `video_upload_service` o `ai_generator` nei test senza toccare rete/API a pagamento).

---

## Setup rapido

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium         # necessario solo se si usa l'export PDF server-side
cp .env.example .env                # inserire GEMINI_API_KEY
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
python -m http.server 5500          # oppure qualsiasi static server / Live Server
# apri http://localhost:5500
```

Nel file `frontend/app.js`, la costante `API_BASE_URL` va puntata all'indirizzo del backend (`http://localhost:8000` in sviluppo).
