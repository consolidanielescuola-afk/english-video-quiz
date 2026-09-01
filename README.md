# EnglishQuiz from YouTube — Architettura e Guida al Progetto

Web app che genera schede didattiche di inglese interattive (video + esercizi) a partire da un URL YouTube **oppure da un video caricato direttamente dall'insegnante**, con livello CEFR e tipologie di esercizio configurabili, correzione istantanea ed export PDF.

### Due percorsi per il video

1. **Link YouTube** (`POST /api/generate`): trascrizione via `youtube-transcript-api`. Soggetto al rate-limiting descritto più sotto sugli hosting cloud gratuiti.
2. **Upload diretto** (`POST /api/generate-from-file`): l'insegnante carica un file video, che viene inviato a Gemini (già usato per generare gli esercizi) anche per la trascrizione, usando la sua comprensione multimodale nativa. Questo percorso **non contatta mai YouTube**, quindi non soffre di alcun rate-limiting esterno — è il percorso consigliato quando quello YouTube risulta inaffidabile. Il file video:
   - viene scritto in un file temporaneo sul server solo il tempo di caricarlo su Gemini, poi cancellato immediatamente (vedi `backend/services/video_upload_service.py`);
   - **non viene mai salvato in modo permanente**, né riproposto ad altri utenti;
   - per la riproduzione nella scheda, resta **esclusivamente nel browser dell'insegnante** (tramite `URL.createObjectURL`) — non viene ricaricato sul server una seconda volta.
   - limiti: max 10 minuti, max 150 MB, deve contenere parlato in inglese comprensibile (altrimenti la scheda non viene generata, con un messaggio di errore chiaro).

---

## 1. Architettura e Tech Stack

### Vista d'insieme

```
┌──────────────┐      HTTPS/JSON      ┌───────────────────┐      ┌──────────────────┐
│   Frontend   │ ───────────────────▶ │   Backend FastAPI  │ ───▶ │  YouTube (oEmbed/ │
│ HTML/CSS/JS  │ ◀─────────────────── │  (Python, async)   │ ◀─── │  Data API v3 +     │
│  (Vanilla +  │                      │                     │      │  transcript-api)   │
│  Tailwind)   │                      └─────────┬───────────┘      └──────────────────┘
└──────────────┘                                │
                                                 ▼
                                       ┌───────────────────┐
                                       │  LLM (Google        │
                                       │  Gemini API)         │
                                       │  → JSON esercizi    │
                                       └───────────────────┘
```

### Perché queste scelte

**Frontend: Vanilla JS + Tailwind CSS (via CDN), niente build step**
Per un progetto di questa dimensione (un form di input + una vista "scheda" dinamica), React aggiunge complessità di build/tooling senza un reale bisogno di componenti riutilizzabili complessi o routing. Vanilla JS con Tailwind è più veloce da avviare, facile da capire e da deployare come file statici (es. Netlify, Vercel, GitHub Pages). Se in futuro la app cresce (gestione utenti, storico schede, dashboard insegnante), consiglio di migrare a **React + Vite** mantenendo la stessa API backend: la separazione netta frontend/backend via REST rende la migrazione indolore.

**Backend: FastAPI (Python)**
- Async nativo → utile perché le chiamate a YouTube e all'LLM sono I/O-bound e possono girare in parallelo o essere gestite con timeout puliti.
- Validazione automatica request/response con **Pydantic** → fondamentale qui perché dobbiamo garantire che l'output dell'LLM rispetti uno schema JSON rigoroso (tipi di esercizio, struttura domande/risposte).
- Documentazione OpenAPI automatica (`/docs`), utile in fase di sviluppo e per un futuro frontend React.
- Alternativa valida: Flask + Pydantic (marshmallow), ma FastAPI riduce boilerplate su validazione e async.

**Recupero sottotitoli/trascrizione: `youtube-transcript-api`**
Libreria Python che recupera i sottotitoli (anche auto-generati) senza bisogno di API key né di scaricare l'audio. È la soluzione più leggera per ottenere il testo su cui basare gli esercizi. Limiti da conoscere:
- Non tutti i video hanno sottotitoli (né manuali né automatici) → va gestito come errore di validazione.
- Non fornisce l'audio/video stesso: per l'embed usiamo semplicemente l'iframe `youtube.com/embed/{id}`, che non richiede alcuna libreria.
- **Importante quando si va in produzione su hosting cloud**: YouTube blocca (`IpBlocked`/`RequestBlocked`) le richieste di trascrizione che arrivano dagli IP dei grandi provider cloud (Render, AWS, GCP, Azure, ecc.) — capita perché quegli IP sono condivisi e spesso usati per scraping massivo, non è un problema specifico di questo progetto. In locale funziona senza problemi (l'IP di casa non è quasi mai bloccato). La soluzione è far passare le richieste da un proxy: la libreria supporta nativamente **Webshare**, che offre un piano gratuito di 10 proxy datacenter (nessuna carta di credito richiesta) — sufficiente per un uso didattico non massivo. Le credenziali vanno in `WEBSHARE_PROXY_USERNAME`/`WEBSHARE_PROXY_PASSWORD` (vedi `.env.example`); se non sono configurate, il codice prova comunque senza proxy (comportamento corretto in locale).

**Verifica esistenza / lingua / durata video: YouTube Data API v3 (oEmbed come fallback leggero)**
- L'endpoint `videos.list` (parte `contentDetails,snippet,status`) della Data API v3 dà in un'unica chiamata: esistenza del video, durata (ISO 8601, es. `PT9M32S`), lingua dichiarata (`defaultAudioLanguage`/`defaultLanguage`) e se è privato/rimosso. Richiede una API key gratuita (quota generosa per un uso didattico).
- Se si vuole evitare la gestione di una API key, si può usare l'endpoint pubblico `oEmbed` (`https://www.youtube.com/oembed?url=...`) solo per verificare che il video esista e sia pubblico, ma **non fornisce la durata** — la durata quindi va comunque recuperata o dedotta dai timestamp dell'ultima riga della trascrizione (soluzione "povera" ma funzionante senza API key). Consiglio comunque la Data API v3 per affidabilità.
- La verifica "lingua inglese" in pratica è più affidabile controllando la lingua della trascrizione recuperata (la maggior parte delle trascrizioni auto-generate riporta il codice lingua, es. `en`, `en-US`) piuttosto che fidarsi solo dei metadati del video, spesso mancanti.

**Generazione esercizi: Google Gemini API (piano gratuito)**
- Si invia la trascrizione (troncata/pulita) + un prompt strutturato che richiede **esclusivamente JSON** conforme a uno schema fisso (vedi `backend/services/ai_generator.py`), rinforzato anche a livello di API con `response_mime_type="application/json"`.
- Scelto al posto di Claude/OpenAI perché la Gemini API mette a disposizione modelli "Flash" gratuiti (nessuna carta di credito richiesta), sufficienti per generare esercizi da un video di massimo 10 minuti. Il codice isola la chiamata all'LLM in un'unica funzione (`_call_llm`), quindi passare a Claude o OpenAI in futuro (se serve più qualità o quota) richiede di toccare solo quella funzione.
- **Nota sui nomi dei modelli**: Google ritira periodicamente i modelli più vecchi (es. `gemini-2.5-flash` è stato dismesso per i nuovi utenti a favore di `gemini-3.6-flash`, il modello usato di default in questo progetto). Se in futuro l'app smette di generare esercizi con un errore "model ... is no longer available", il messaggio di Google indica sempre il nome del modello sostitutivo da usare: basta aggiornare la variabile d'ambiente `GEMINI_MODEL` su Render (Settings → Environment) senza toccare il codice.
- Compromesso da conoscere: i modelli gratuiti "Flash" sono meno raffinati di un modello di punta nel calibrare con precisione i livelli CEFR più alti (C1) o nel generare distrattori molto sottili per le multiple choice — per un uso didattico standard restano comunque adeguati.

**Generazione PDF: Playwright (server-side) come soluzione principale**
- La scheda è già una pagina HTML/CSS ben impaginata: la soluzione più robusta è **renderizzarla in un browser headless (Playwright) e stamparla in PDF** (`page.pdf()`), perché rispetta perfettamente CSS, `@media print`, interruzioni di pagina (`page-break-*`) e permette di generare due varianti (studente senza soluzioni / insegnante con soluzioni in pagina separata) semplicemente passando un parametro che aggiunge/rimuove una classe CSS prima dello screenshot.
- Alternativa più semplice senza backend aggiuntivo: **`html2pdf.js`** (client-side, combina `html2canvas` + `jsPDF`) — più rapida da integrare ma con limiti di qualità tipografica su contenuti lunghi (rasterizza il contenuto, testo non selezionabile, meno controllo sui page-break). La includo come fallback per chi vuole evitare Playwright.
- Sconsiglio `wkhtmltopdf` (progetto non più mantenuto attivamente) e la generazione "manuale" con ReportLab (troppo lavoro per replicare un layout HTML/CSS già pronto).
**Upload diretto del video: Gemini Files API (`google-genai`)**
- Stesso client Gemini già usato per generare gli esercizi: `client.files.upload(file=path)` carica il video, poi si attende (polling su `client.files.get`) che passi da `PROCESSING` ad `ACTIVE`, infine `client.models.generate_content(model=..., contents=[uploaded_file, prompt])` produce la trascrizione, chiedendo esplicitamente a Gemini di rispondere con sentinel (`NONENGLISH`, `NOSPEECH`) se il contenuto non è utilizzabile, così la validazione è la stessa gestione di errore già usata per il percorso YouTube.
- La durata del video viene letta con `mutagen` (nessuna dipendenza da ffmpeg/binari esterni); se il formato non è riconosciuto si lascia semplicemente proseguire (Gemini stesso segnalerà eventuali problemi con il file).
- Endpoint `multipart/form-data` (richiede il pacchetto `python-multipart`) invece di JSON, per poter inviare il file binario insieme a livello CEFR e tipologie di esercizio.

- **Nota per il deploy su Render (o hosting nativi simili, non-Docker)**: Playwright scarica il browser Chromium in `~/.cache/ms-playwright` per default — una cartella FUORI dalla directory del progetto. Su Render, solo la directory del progetto viene "promossa" dall'ambiente di build a quello di runtime: il browser scaricato durante il build va quindi perso, e al primo utilizzo compare l'errore `BrowserType.launch: Executable doesn't exist`. La soluzione è impostare la variabile d'ambiente `PLAYWRIGHT_BROWSERS_PATH=0`, che dice a Playwright di installare il browser dentro la cartella del pacchetto stesso (quindi dentro il progetto, e quindi promossa correttamente). Va impostata *prima* del deploy (il browser va scaricato di nuovo con il nuovo percorso) — è già in `render.yaml` e va configurata a mano su Render se si crea il servizio dalla dashboard.

### Tabella riassuntiva

| Livello | Tecnologia | Motivazione |
|---|---|---|
| Frontend | Vanilla JS + Tailwind CSS (CDN) | Zero build, rapido da avviare, facile migrazione futura a React |
| Backend | FastAPI (Python 3.11+) | Async, validazione Pydantic, OpenAPI automatico |
| Trascrizione | `youtube-transcript-api` | Nessuna API key, supporta sottotitoli auto-generati |
| Metadati video (durata/lingua/esistenza) | YouTube Data API v3 | Unica fonte affidabile per durata e stato del video |
| Generazione esercizi | Google Gemini API (`gemini-3.6-flash`, gratuito) | Nessun costo per uso didattico non massivo, output JSON vincolato |
| Correzione | JS lato client | Istantanea, nessuna latenza di rete |
| PDF | Playwright (server) + html2pdf.js (fallback client) | Fedeltà di stampa massima; fallback semplice |
| Hosting suggerito | Frontend: Netlify/Vercel — Backend: Render/Fly.io/Railway | Deploy gratuito/economico, HTTPS incluso |

---

## 2. Struttura del progetto

```
english-video-quiz/
├── README.md
├── frontend/
│   ├── index.html          # pagina unica: form input + vista scheda (SPA leggera)
│   ├── styles.css          # stili custom minimi (oltre Tailwind CDN) + regole @media print
│   └── app.js              # logica: fetch API, rendering dinamico, correzione, export PDF
│
└── backend/
    ├── main.py              # entrypoint FastAPI, definizione endpoint
    ├── requirements.txt
    ├── .env.example         # YOUTUBE_API_KEY, GEMINI_API_KEY
    ├── models/
    │   └── schemas.py       # Pydantic: request/response, Exercise, GenerateRequest...
    └── services/
        ├── youtube_service.py   # estrazione video id, validazione, trascrizione
        ├── ai_generator.py      # prompt building + chiamata LLM + parsing/validazione JSON
        └── pdf_service.py       # generazione PDF via Playwright (student/teacher)
```

Convenzione: ogni "servizio" del backend fa una cosa sola ed è testabile in isolamento (si può mockare `youtube_service` o `ai_generator` nei test senza toccare rete/API a pagamento).

---

## Setup rapido

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium         # necessario solo se si usa l'export PDF server-side
cp .env.example .env                # inserire le chiavi API
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
python -m http.server 5500          # oppure qualsiasi static server / Live Server
# apri http://localhost:5500
```

Nel file `frontend/app.js`, la costante `API_BASE_URL` va puntata all'indirizzo del backend (`http://localhost:8000` in sviluppo).
