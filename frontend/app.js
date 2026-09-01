// ============================================================================
// EnglishQuiz from YouTube — frontend logic
// Vanilla JS, nessuna dipendenza esterna a parte Tailwind (CDN, solo stile).
// ============================================================================

const API_BASE_URL = "http://localhost:8000"; // <-- puntare al backend in produzione

// ---- Stato applicazione ---------------------------------------------------
let currentWorksheet = null; // ultima scheda generata (JSON dal backend)
let isCorrected = false;

// ---- Riferimenti DOM --------------------------------------------------------
const viewConfig = document.getElementById("view-config");
const viewWorksheet = document.getElementById("view-worksheet");
const backToConfigBtn = document.getElementById("backToConfigBtn");

const generateForm = document.getElementById("generateForm");
const generateBtn = document.getElementById("generateBtn");
const generateBtnText = document.getElementById("generateBtnText");
const generateSpinner = document.getElementById("generateSpinner");
const formError = document.getElementById("formError");

const worksheetTitle = document.getElementById("worksheetTitle");
const worksheetMeta = document.getElementById("worksheetMeta");
const levelBadge = document.getElementById("levelBadge");
const videoIframe = document.getElementById("videoIframe");
const exercisesContainer = document.getElementById("exercisesContainer");

const correctBtn = document.getElementById("correctBtn");
const resetBtn = document.getElementById("resetBtn");
const downloadPdfBtn = document.getElementById("downloadPdfBtn");
const pdfBtnText = document.getElementById("pdfBtnText");
const scoreBanner = document.getElementById("scoreBanner");
const scoreValue = document.getElementById("scoreValue");
const scoreDetail = document.getElementById("scoreDetail");

// ---- Etichette leggibili per tipo esercizio --------------------------------
const EXERCISE_LABELS = {
  multiple_choice: "Multiple Choice",
  true_false: "True / False",
  gap_fill: "Gap Fill",
  matching: "Matching",
  open_ended: "Open Ended",
};

// =============================================================================
// 1. GENERAZIONE SCHEDA
// =============================================================================

generateForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  hideError();

  const youtubeUrl = document.getElementById("youtubeUrl").value.trim();
  const level = generateForm.querySelector('input[name="level"]:checked').value;
  const exerciseTypes = Array.from(
    generateForm.querySelectorAll('input[name="exerciseTypes"]:checked')
  ).map((el) => el.value);

  if (exerciseTypes.length === 0) {
    showError("Seleziona almeno una tipologia di esercizio.");
    return;
  }

  setLoading(true);

  try {
    const res = await fetch(`${API_BASE_URL}/api/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        youtube_url: youtubeUrl,
        level: level,
        exercise_types: exerciseTypes,
      }),
    });

    const data = await res.json();

    if (!res.ok) {
      // Il backend restituisce {"detail": "messaggio leggibile"} sugli errori di validazione
      // (video non trovato, non in inglese, durata > 10 minuti, nessuna trascrizione...)
      showError(data.detail || "Si è verificato un errore durante la generazione.");
      return;
    }

    currentWorksheet = data;
    renderWorksheet(data);
    switchView("worksheet");
  } catch (err) {
    console.error(err);
    showError("Impossibile contattare il server. Riprova più tardi.");
  } finally {
    setLoading(false);
  }
});

function setLoading(loading) {
  generateBtn.disabled = loading;
  generateSpinner.classList.toggle("hidden", !loading);
  generateBtnText.textContent = loading ? "Generazione in corso..." : "Genera";
}

function showError(msg) {
  formError.textContent = msg;
  formError.classList.remove("hidden");
}
function hideError() {
  formError.classList.add("hidden");
}

// =============================================================================
// 2. RENDERING DELLA SCHEDA
// =============================================================================

function renderWorksheet(data) {
  isCorrected = false;
  resetBtn.classList.add("hidden");
  scoreBanner.classList.add("hidden");
  correctBtn.disabled = false;
  correctBtn.textContent = "Correggi";

  worksheetTitle.textContent = data.video.title;
  worksheetMeta.textContent = `${data.video.channel} · ${formatDuration(data.video.duration_seconds)}`;
  levelBadge.textContent = data.level;
  videoIframe.src = `https://www.youtube.com/embed/${data.video.id}`;

  exercisesContainer.innerHTML = "";

  // Raggruppa gli esercizi per tipo così da mostrare una sezione per tipologia
  const grouped = {};
  for (const ex of data.exercises) {
    if (!grouped[ex.type]) grouped[ex.type] = [];
    grouped[ex.type].push(ex);
  }

  for (const [type, items] of Object.entries(grouped)) {
    const section = document.createElement("div");
    section.innerHTML = `<h3 class="text-base font-semibold text-slate-700 mb-3">${EXERCISE_LABELS[type] || type}</h3>`;
    const list = document.createElement("div");
    list.className = "space-y-4";

    items.forEach((ex, idx) => {
      list.appendChild(renderExercise(ex, idx));
    });

    section.appendChild(list);
    exercisesContainer.appendChild(section);
  }
}

function renderExercise(ex, idx) {
  const wrapper = document.createElement("div");
  wrapper.className = "exercise-item";
  wrapper.dataset.exId = ex.id;
  wrapper.dataset.exType = ex.type;

  switch (ex.type) {
    case "multiple_choice":
      wrapper.innerHTML = `
        <p class="font-medium mb-2">${idx + 1}. ${ex.question}</p>
        <div class="space-y-1.5">
          ${ex.options
            .map(
              (opt, i) => `
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="radio" name="${ex.id}" value="${i}" class="accent-indigo-600" />
              <span>${opt}</span>
            </label>`
            )
            .join("")}
        </div>`;
      break;

    case "true_false":
      wrapper.innerHTML = `
        <p class="font-medium mb-2">${idx + 1}. ${ex.statement}</p>
        <div class="flex gap-4">
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="radio" name="${ex.id}" value="true" class="accent-indigo-600" /> True
          </label>
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="radio" name="${ex.id}" value="false" class="accent-indigo-600" /> False
          </label>
        </div>`;
      break;

    case "gap_fill": {
      // ex.text contiene dei segnaposto "___" nell'ordine in cui compaiono i blank
      let gapCounter = 0;
      const html = ex.text.split("___").reduce((acc, chunk, i, arr) => {
        acc += escapeHtml(chunk);
        if (i < arr.length - 1) {
          acc += `<input type="text" class="gap-input" data-gap-index="${gapCounter}" autocomplete="off" />`;
          gapCounter++;
        }
        return acc;
      }, "");
      wrapper.innerHTML = `<p class="font-medium leading-8">${idx + 1}. ${html}</p>`;
      break;
    }

    case "matching": {
      const rightOptionsShuffled = shuffle(ex.pairs.map((p) => p.right));
      const rows = ex.pairs
        .map(
          (p, i) => `
        <div class="flex items-center gap-3">
          <span class="w-40 shrink-0">${p.left}</span>
          <select data-pair-index="${i}" class="border border-slate-300 rounded-md px-2 py-1 flex-1">
            <option value="">— seleziona —</option>
            ${rightOptionsShuffled
              .map((opt) => `<option value="${escapeHtmlAttr(opt)}">${opt}</option>`)
              .join("")}
          </select>
        </div>`
        )
        .join("");
      wrapper.innerHTML = `<p class="font-medium mb-2">${idx + 1}. Abbina ogni elemento alla definizione corretta.</p><div class="space-y-2">${rows}</div>`;
      break;
    }

    case "open_ended":
      wrapper.innerHTML = `
        <p class="font-medium mb-2">${idx + 1}. ${ex.question}</p>
        <textarea class="w-full border border-slate-300 rounded-lg px-3 py-2 min-h-[80px]" placeholder="Scrivi la tua risposta..."></textarea>
        <div class="model-answer-box hidden" data-model-answer>💡 Possibile risposta: ${ex.model_answer}</div>`;
      break;
  }

  return wrapper;
}

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")} min`;
}

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeHtmlAttr(str) {
  return escapeHtml(str);
}

// =============================================================================
// 3. CORREZIONE ISTANTANEA
// =============================================================================

correctBtn.addEventListener("click", () => {
  if (!currentWorksheet) return;

  let correctCount = 0;
  let gradableCount = 0;

  document.querySelectorAll(".exercise-item").forEach((wrapper) => {
    const exId = wrapper.dataset.exId;
    const exType = wrapper.dataset.exType;
    const ex = currentWorksheet.exercises.find((e) => e.id === exId);
    if (!ex) return;

    if (exType === "multiple_choice") {
      gradableCount++;
      const selected = wrapper.querySelector(`input[name="${exId}"]:checked`);
      const isRight = selected && Number(selected.value) === ex.correct_index;
      if (isRight) correctCount++;
      markRadioGroup(wrapper, exId, ex.correct_index.toString());
    }

    if (exType === "true_false") {
      gradableCount++;
      const selected = wrapper.querySelector(`input[name="${exId}"]:checked`);
      const isRight = selected && selected.value === String(ex.correct);
      if (isRight) correctCount++;
      markRadioGroup(wrapper, exId, String(ex.correct));
    }

    if (exType === "gap_fill") {
      const inputs = wrapper.querySelectorAll(".gap-input");
      inputs.forEach((input) => {
        gradableCount++;
        const gapIndex = Number(input.dataset.gapIndex);
        const expected = ex.blanks[gapIndex].answers.map((a) => normalize(a));
        const userVal = normalize(input.value);
        const isRight = expected.includes(userVal);
        if (isRight) correctCount++;
        input.classList.remove("answer-correct", "answer-incorrect");
        input.classList.add(isRight ? "answer-correct" : "answer-incorrect");
        input.disabled = true;
        if (!isRight) input.title = `Risposta corretta: ${ex.blanks[gapIndex].answers[0]}`;
      });
    }

    if (exType === "matching") {
      const selects = wrapper.querySelectorAll("select[data-pair-index]");
      selects.forEach((select) => {
        gradableCount++;
        const pairIndex = Number(select.dataset.pairIndex);
        const expected = ex.pairs[pairIndex].right;
        const isRight = select.value === expected;
        if (isRight) correctCount++;
        select.classList.remove("answer-correct", "answer-incorrect");
        select.classList.add(isRight ? "answer-correct" : "answer-incorrect");
        select.disabled = true;
      });
    }

    if (exType === "open_ended") {
      // Non auto-correggibile in modo affidabile: mostriamo la risposta modello
      // per l'auto-valutazione dello studente, esclusa dal punteggio.
      const textarea = wrapper.querySelector("textarea");
      const modelBox = wrapper.querySelector("[data-model-answer]");
      if (textarea) textarea.disabled = true;
      if (modelBox) modelBox.classList.remove("hidden");
    }
  });

  const percentage = gradableCount > 0 ? Math.round((correctCount / gradableCount) * 100) : 0;
  scoreValue.textContent = `${percentage}%`;
  scoreDetail.textContent = `${correctCount} / ${gradableCount} risposte corrette`;
  scoreBanner.classList.remove("hidden");

  isCorrected = true;
  correctBtn.disabled = true;
  resetBtn.classList.remove("hidden");
});

function markRadioGroup(wrapper, name, correctValue) {
  wrapper.querySelectorAll(`input[name="${name}"]`).forEach((input) => {
    const label = input.closest("label");
    input.disabled = true;
    if (input.value === correctValue) {
      label.classList.add("answer-correct");
    } else if (input.checked) {
      label.classList.add("answer-incorrect");
    }
  });
}

function normalize(str) {
  return (str || "").trim().toLowerCase().replace(/\s+/g, " ");
}

resetBtn.addEventListener("click", () => {
  if (currentWorksheet) renderWorksheet(currentWorksheet);
});

// =============================================================================
// 4. EXPORT PDF
// =============================================================================

downloadPdfBtn.addEventListener("click", async () => {
  if (!currentWorksheet) return;
  pdfBtnText.textContent = "Generazione PDF...";
  downloadPdfBtn.disabled = true;

  try {
    // Opzione principale: PDF generato server-side con Playwright (vedi backend/services/pdf_service.py)
    // per un'impaginazione fedele e la versione con soluzioni su pagina separata.
    const res = await fetch(`${API_BASE_URL}/api/export-pdf`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ worksheet: currentWorksheet, include_answers: false }),
    });

    if (!res.ok) throw new Error("PDF generation failed");

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slugify(currentWorksheet.video.title)}-${currentWorksheet.level}.pdf`;
    a.click();
    window.URL.revokeObjectURL(url);
  } catch (err) {
    console.warn("Export server-side non disponibile, uso fallback client (window.print).", err);
    // Fallback minimo senza librerie aggiuntive: usa il CSS @media print già definito in styles.css.
    // Per un fallback client "vero PDF" senza backend si può integrare html2pdf.js:
    //   <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
    //   html2pdf().from(document.getElementById('worksheetPrintArea')).save();
    window.print();
  } finally {
    pdfBtnText.textContent = "Scarica PDF";
    downloadPdfBtn.disabled = false;
  }
});

function slugify(str) {
  return str.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "").slice(0, 60);
}

// =============================================================================
// 5. NAVIGAZIONE TRA VISTE
// =============================================================================

function switchView(view) {
  if (view === "worksheet") {
    viewConfig.classList.add("hidden");
    viewWorksheet.classList.remove("hidden");
    backToConfigBtn.classList.remove("hidden");
  } else {
    viewConfig.classList.remove("hidden");
    viewWorksheet.classList.add("hidden");
    backToConfigBtn.classList.add("hidden");
  }
}

backToConfigBtn.addEventListener("click", () => switchView("config"));
