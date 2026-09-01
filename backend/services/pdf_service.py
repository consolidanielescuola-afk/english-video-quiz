"""
Generazione del PDF stampabile della scheda, tramite Playwright (Chromium headless):
la stessa pagina HTML viene renderizzata e "stampata" in PDF, così l'impaginazione
rispecchia esattamente CSS e page-break definiti qui sotto.

Due modalità:
  - include_answers=False -> versione "studente": nessuna soluzione visibile.
  - include_answers=True  -> versione "insegnante": esercizi + pagina finale
    con interruzione di pagina (page-break-before) contenente tutte le soluzioni.

Il video (caricato dall'insegnante, mai salvato sul server) non può ovviamente
essere "incorporato" in un PDF: nella scheda compaiono solo titolo e durata.
"""

from playwright.async_api import async_playwright

from models.schemas import Worksheet


def _render_solution_html(ex) -> str:
    """Riga di soluzione mostrata direttamente sotto l'esercizio, nella versione
    "con soluzioni": stesso contenuto del "Mostra soluzione" della pagina web, così
    la differenza rispetto alla versione studente è visibile subito, non solo
    nell'answer key finale."""
    if ex.type == "multiple_choice":
        return f"<div class='solution'>✔ Risposta corretta: {ex.options[ex.correct_index]}</div>"
    if ex.type == "true_false":
        return f"<div class='solution'>✔ Risposta corretta: {'True' if ex.correct else 'False'}</div>"
    if ex.type == "gap_fill":
        answers = ", ".join(b.answers[0] for b in ex.blanks)
        return f"<div class='solution'>✔ Risposte corrette: {answers}</div>"
    if ex.type == "matching":
        pairs_str = ", ".join(f"{p.left} → {p.right}" for p in ex.pairs)
        return f"<div class='solution'>✔ Soluzione: {pairs_str}</div>"
    if ex.type == "open_ended":
        return f"<div class='solution'>💡 Possibile risposta: {ex.model_answer}</div>"
    return ""


def _render_exercise_html(ex, index: int, include_answers: bool) -> str:
    solution_html = _render_solution_html(ex) if include_answers else ""

    if ex.type == "multiple_choice":
        options = "".join(f"<div class='option'>○ {opt}</div>" for opt in ex.options)
        return f"<div class='exercise'><p class='q'>{index}. {ex.question}</p>{options}{solution_html}</div>"

    if ex.type == "true_false":
        return f"""<div class='exercise'><p class='q'>{index}. {ex.statement}</p>
            <div class='option'>○ True &nbsp;&nbsp;&nbsp; ○ False</div>{solution_html}</div>"""

    if ex.type == "gap_fill":
        text = ex.text.replace("___", "<span class='blank'>&nbsp;</span>")
        return f"<div class='exercise'><p class='q'>{index}. {text}</p>{solution_html}</div>"

    if ex.type == "matching":
        left_col = "".join(f"<div class='match-row'>{i + 1}. {p.left}</div>" for i, p in enumerate(ex.pairs))
        right_labels = "ABCDEFGH"
        right_col = "".join(f"<div class='match-row'>{right_labels[i]}. {p.right}</div>" for i, p in enumerate(ex.pairs))
        return f"""<div class='exercise'><p class='q'>{index}. Match each item on the left with the correct definition on the right.</p>
            <div class='match-grid'><div>{left_col}</div><div>{right_col}</div></div>{solution_html}</div>"""

    if ex.type == "open_ended":
        return f"""<div class='exercise'><p class='q'>{index}. {ex.question}</p>
            <div class='answer-lines'></div><div class='answer-lines'></div>{solution_html}</div>"""

    return ""


def _render_answer_key_html(exercises) -> str:
    rows = []
    for i, ex in enumerate(exercises, start=1):
        if ex.type == "multiple_choice":
            rows.append(f"{i}. {ex.options[ex.correct_index]}")
        elif ex.type == "true_false":
            rows.append(f"{i}. {'True' if ex.correct else 'False'}")
        elif ex.type == "gap_fill":
            answers = ", ".join(b.answers[0] for b in ex.blanks)
            rows.append(f"{i}. {answers}")
        elif ex.type == "matching":
            right_labels = "ABCDEFGH"
            pairs_str = ", ".join(f"{j + 1}-{right_labels[j]}" for j in range(len(ex.pairs)))
            rows.append(f"{i}. {pairs_str}")
        elif ex.type == "open_ended":
            rows.append(f"{i}. (risposta libera) Esempio: {ex.model_answer}")
    return "".join(f"<div class='answer-row'>{row}</div>" for row in rows)


def _build_html(worksheet: Worksheet, include_answers: bool) -> str:
    exercises_html = "".join(
        _render_exercise_html(ex, i + 1, include_answers) for i, ex in enumerate(worksheet.exercises)
    )

    answer_key_html = ""
    if include_answers:
        answer_key_html = f"""
        <div class='page-break'></div>
        <h2>Answer Key</h2>
        {_render_answer_key_html(worksheet.exercises)}
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<style>
  @page {{ size: A4; margin: 20mm 18mm; }}
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #1e293b; font-size: 12pt; line-height: 1.5; }}
  h1 {{ font-size: 18pt; margin-bottom: 2px; }}
  .meta {{ color: #64748b; font-size: 10pt; margin-bottom: 4px; }}
  .badge {{ display: inline-block; background: #eef2ff; color: #4338ca; font-weight: bold;
            padding: 2px 10px; border-radius: 12px; font-size: 10pt; margin-bottom: 16px; }}
  .exercise {{ margin-bottom: 16px; break-inside: avoid; }}
  .q {{ font-weight: 600; margin-bottom: 6px; }}
  .option {{ margin-left: 12px; margin-bottom: 3px; }}
  .blank {{ display: inline-block; min-width: 90px; border-bottom: 1.5px solid #1e293b; margin: 0 2px; }}
  .match-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px 24px; margin-left: 12px; }}
  .match-row {{ margin-bottom: 3px; }}
  .answer-lines {{ border-bottom: 1px solid #cbd5e1; height: 18px; margin: 4px 0; }}
  .page-break {{ break-before: page; }}
  .answer-row {{ margin-bottom: 4px; }}
  .solution {{ margin-top: 6px; padding: 4px 8px; background: #fffbeb; border-left: 3px solid #f59e0b;
               color: #78350f; font-size: 10.5pt; border-radius: 3px; }}
  a {{ color: #4338ca; }}
</style>
</head>
<body>
  <h1>{worksheet.video.title}</h1>
  <p class="meta">Video caricato dall'insegnante &middot; durata {worksheet.video.duration_seconds // 60} min</p>
  <span class="badge">CEFR {worksheet.level.value}</span>
  {"<span class='badge' style='background:#dcfce7;color:#166534;margin-left:6px;'>Versione con soluzioni</span>" if include_answers else ""}
  {exercises_html}
  {answer_key_html}
</body>
</html>"""


async def render_worksheet_pdf(worksheet: Worksheet, include_answers: bool = False) -> bytes:
    html = _build_html(worksheet, include_answers)

    async with async_playwright() as p:
        # NB: dalla v1.49 circa, Playwright usa di default un binario separato
        # ("chromium-headless-shell") per il lancio headless, che sul build di
        # Render non viene scaricato dal comando `playwright install chromium`
        # (che scarica solo il Chromium "completo"). Specificando esplicitamente
        # channel="chromium" si forza l'uso del Chromium completo già installato,
        # senza dover cambiare il build command su Render.
        browser = await p.chromium.launch(channel="chromium")
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            pdf_bytes = await page.pdf(format="A4", print_background=True)
        finally:
            await browser.close()

    return pdf_bytes
