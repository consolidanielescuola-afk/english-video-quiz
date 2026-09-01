// ============================================================================
// EnglishQuiz — frontend logic
// Vanilla JS, nessuna dipendenza esterna a parte Tailwind (CDN, solo stile).
// Il video è sempre caricato dall'insegnante: resta nel browser (object URL),
// non viene mai salvato sul server né ricaricato una seconda volta.
// ============================================================================

const API_BASE_URL = "https://english-video-quiz-backend.onrender.com";

const EXERCISE_TYPES = ["multiple_choice", "true_false", "gap_fill", "matching", "open_ended"];

// ---- Stato applicazione ---------------------------------------------------
let currentWorksheet = null; // ultima scheda generata (JSON dal backend)
let isCorrected = false;
let solutionVisible = false;
let currentVideoObjectUrl = null; // URL locale (URL.createObjectURL) del video caricato

// ---- Riferimenti DOM --------------------------------------------------------
const viewConfig = document.getElementById("view-config");
const viewWorksheet = document.getElementById("view-worksheet");
const backToConfigBtn = document.getElementById("backToConfigBtn");

const generateForm = document.getElementById("generateForm");
const generateBtn = document.getElementById("generateBtn");
const generateBtnText = document.getElementById("generateBtnText");
const generateSpinner = document.getElementById("generateSpinner");
const formError = document.getElementById("formError");
const videoFileInput = document.getElementById("videoFile");

const worksheetTitle = document.getElementById("worksheetTitle");
const worksheetMeta = document.getElementById("worksheetMeta");
const levelBadge = document.getElementById("levelBadge");
const videoPlayer = document.getElementById("videoPlayer");
const exercisesContainer = document.getElementById("exercisesContainer");

const correctBtn = document.getElementById("correctBtn");
const resetBtn = document.getElementById("resetBtn");
const showSolutionBtn = document.getElementById("showSolutionBtn");
const downloadPdfBtn = document.getElementById("downloadPdfBtn");
const pdfBtnText = document.getElementById("pdfBtnText");
const downloadSolutionPdfBtn = document.getElementById("downloadSolutionPdfBtn");
const pdfSolutionBtnText = document.getElementById("pdfSolutionBtnText");
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

  const level = generateForm.querySelector('input[name="level"]:checked').value;

  const exerciseCounts = {};
  let total = 0;
  for (const type of EXERCISE_TYPES) {
    const input = generateForm.querySelector(`input[name="count_${type}"]`);
    const count = Math.max(0, Math.min(10, parseInt(input.value, 10) || 0));
    input.value = count; // normalizza eventuali valori fuori range digitati a mano
    exerciseCounts[type] = count;
    total += count;
  }

  if (total === 0) {
    showError("Scegli almeno una domanda in una tipologia di esercizio.");
    return;
  }

  if (!videoFileInput.files[0]) {
    showError("Seleziona un file video da caricare.");
    return;
  }

  setLoading(true);

  try {
    const file = videoFileInput.files[0];
    const formData = new FormData();
    formData.append("level", level);
    formData.append("exercise_counts", JSON.stringify(exerciseCounts));
    formData.append("video", file);

    const res = await fetch(`${API_BASE_URL}/api/generate-from-file`, {
      method: "POST",
      body: formData, // niente header Content-Type: il browser imposta il boundary multipart corretto
    });

    const data = await res.json();

    if (!res.ok) {
      // Il backend restituisce {"detail": "messaggio leggibile"} sugli errori di validazione
      // (file troppo grande, non in inglese, nessun parlato rilevato...)
      showError(data.detail || "Si è verificato un errore durante la generazione.");
      return;
    }

    // Il video resta nel browser: creiamo un URL locale dal file selezionato (il file è
    // già stato inviato/consumato dal fetch qui sopra e cancellato lato server dopo la
    // trascrizione, non viene mai rispedito al server per la riproduzione).
    if (currentVideoObjectUrl) URL.revokeObjectURL(currentVideoObjectUrl);
    currentVideoObjectUrl = URL.createObjectURL(file);

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
  solutionVisible = false;
  resetBtn.classList.add("hidden");
  showSolutionBtn.classList.add("hidden");
  showSolutionBtn.textContent = "Mostra soluzione";
  scoreBanner.classList.add("hidden");
  correctBtn.disabled = false;
  correctBtn.textContent = "Correggi";

  worksheetTitle.textContent = data.video.title;
  worksheetMeta.textContent = formatDuration(data.video.duration_seconds);
  levelBadge.textContent = data.level;

  videoPlayer.pause();
  if (currentVideoObjectUrl) videoPlayer.src = currentVideoObjectUrl;

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
        </div>
        <div class="solution-text hidden" data-solution>✅ Risposta corretta: ${ex.options[ex.correct_index]}</div>`;
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
        </div>
        <div class="solution-text hidden" data-solution>✅ Risposta corretta: ${ex.correct ? "True" : "False"}</div>`;
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
      const answersList = ex.blanks.map((b) => b.answers[0]).join(", ");
      wrapper.innerHTML = `
        <p class="font-medium leading-8">${idx + 1}. ${html}</p>
        <div class="solution-text hidden" data-solution>✅ Risposte corrette: ${answersList}</div>`;
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
      const solutionRows = ex.pairs.map((p) => `${p.left} → ${p.right}`).join("<br />");
      wrapper.innerHTML = `
        <p class="font-medium mb-2">${idx + 1}. Abbina ogni elemento alla definizione corretta.</p>
        <div class="space-y-2">${rows}</div>
        <div class="solution-text hidden" data-solution>✅ Soluzione:<br />${solutionRows}</div>`;
      break;
    }

    case "open_ended":
      wrapper.innerHTML = `
        <p class="font-medium mb-2">${idx + 1}. ${ex.question}</p>
        <textarea class="w-full border border-slate-300 rounded-lg px-3 py-2 min-h-[80px]" placeholder="Scrivi la tua risposta..."></textarea>
        <div class="solution-text hidden" data-solution>💡 Possibile risposta: ${ex.model_answer}</div>`;
      break;
  }

  return wrapper;
}

function formatDuration(seconds) {
  if (!seconds) return "";
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
// 3. CORREZIONE ISTANTANEA + MOSTRA SOLUZIONE
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
      // Non auto-correggibile in modo affidabile: la risposta modello si vede solo
      // premendo "Mostra soluzione", esclusa dal punteggio.
      const textarea = wrapper.querySelector("textarea");
      if (textarea) textarea.disabled = true;
    }
  });

  const percentage = gradableCount > 0 ? Math.round((correctCount / gradableCount) * 100) : 0;
  scoreValue.textContent = `${percentage}%`;
  scoreDetail.textContent = `${correctCount} / ${gradableCount} risposte corrette`;
  scoreBanner.classList.remove("hidden");

  isCorrected = true;
  correctBtn.disabled = true;
  resetBtn.classList.remove("hidden");
  showSolutionBtn.classList.remove("hidden");
});

showSolutionBtn.addEventListener("click", () => {
  solutionVisible = !solutionVisible;
  document.querySelectorAll("[data-solution]").forEach((el) => {
    el.classList.toggle("hidden", !solutionVisible);
  });
  showSolutionBtn.textContent = solutionVisible ? "Nascondi soluzione" : "Mostra soluzione";
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
// 4. EXPORT PDF (versione studente + versione con soluzioni)
// =============================================================================

async function exportPdf(includeAnswers, button, buttonTextEl, defaultLabel) {
  if (!currentWorksheet) return;
  buttonTextEl.textContent = "Generazione PDF...";
  button.disabled = true;

  try {
    const res = await fetch(`${API_BASE_URL}/api/export-pdf`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ worksheet: currentWorksheet, include_answers: includeAnswers }),
    });

    if (!res.ok) throw new Error("PDF generation failed");

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const suffix = includeAnswers ? "-soluzioni" : "";
    a.download = `${slugify(currentWorksheet.video.title)}-${currentWorksheet.level}${suffix}.pdf`;
    a.click();
    window.URL.revokeObjectURL(url);
  } catch (err) {
    console.warn("Export PDF non disponibile, uso fallback client (window.print).", err);
    // Fallback minimo senza librerie aggiuntive: usa il CSS @media print già definito in styles.css.
    window.print();
  } finally {
    buttonTextEl.textContent = defaultLabel;
    button.disabled = false;
  }
}

downloadPdfBtn.addEventListener("click", () => {
  exportPdf(false, downloadPdfBtn, pdfBtnText, "Scarica PDF");
});

downloadSolutionPdfBtn.addEventListener("click", () => {
  exportPdf(true, downloadSolutionPdfBtn, pdfSolutionBtnText, "Scarica soluzione PDF");
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
