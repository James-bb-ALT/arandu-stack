"use strict";
const $ = (id) => document.getElementById(id);
const percent = (value) =>
  value == null
    ? "—"
    : `${(value * 100).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 2 })}%`;
const phaseNames = {
  environment: "Preparando ambiente",
  validating: "Validando dataset",
  loading_checkpoint: "Restaurando checkpoint",
  caching_encoder: "Preparando representações",
  training: "Treinamento na RTX",
  saving_checkpoint: "Salvando checkpoint",
  trained: "Treino concluído",
  evaluating: "Avaliando os dois modelos",
  exporting: "Verificando exportação",
  completed: "Experimento concluído",
  failed: "Experimento interrompido",
};
let samples = [],
  lastStatus = null,
  uploadBlob = null,
  recorder = null,
  recordStream = null,
  recordTimeout = null,
  busy = false;
async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("O servidor não respondeu corretamente.");
  }
  if (!response.ok)
    throw new Error(data.detail || "Não foi possível concluir a operação.");
  return data;
}
function showError(message) {
  $("errorMessage").textContent = message;
  $("errorMessage").hidden = !message;
  if (message) $("errorMessage").scrollIntoView({ block: "nearest" });
}
function normalize(text) {
  return text
    .normalize("NFC")
    .toLocaleLowerCase("pt-BR")
    .replace(/\p{P}/gu, "")
    .trim();
}
function markTranscript(container, text, reference) {
  container.replaceChildren();
  container.classList.remove("empty");
  if (!text) {
    container.textContent = "Nenhuma fala reconhecida.";
    return;
  }
  if (!reference) {
    container.textContent = text;
    return;
  }
  const raw = text.trim().split(/\s+/),
    hyp = raw.map(normalize),
    ref = reference.trim().split(/\s+/).map(normalize);
  const d = Array.from({ length: ref.length + 1 }, () =>
    Array(hyp.length + 1).fill(0),
  );
  for (let i = 0; i <= ref.length; i++) d[i][0] = i;
  for (let j = 0; j <= hyp.length; j++) d[0][j] = j;
  for (let i = 1; i <= ref.length; i++)
    for (let j = 1; j <= hyp.length; j++)
      d[i][j] = Math.min(
        d[i - 1][j] + 1,
        d[i][j - 1] + 1,
        d[i - 1][j - 1] + (ref[i - 1] !== hyp[j - 1]),
      );
  let i = ref.length,
    j = hyp.length;
  const wrong = new Set();
  while (i || j) {
    if (i && j && d[i][j] === d[i - 1][j - 1] + (ref[i - 1] !== hyp[j - 1])) {
      if (ref[i - 1] !== hyp[j - 1]) wrong.add(j - 1);
      i--;
      j--;
    } else if (i && d[i][j] === d[i - 1][j] + 1) i--;
    else {
      wrong.add(j - 1);
      j--;
    }
  }
  raw.forEach((word, index) => {
    if (index) container.append(" ");
    const element = document.createElement(wrong.has(index) ? "mark" : "span");
    element.textContent = word;
    container.append(element);
  });
}
function drawCurve(history) {
  const usable = history.filter((x) => x.validation_wer != null);
  if (usable.length < 2) return;
  const width = 450,
    height = 108,
    pad = 14,
    max = Math.max(0.01, ...usable.map((x) => x.validation_wer)) * 1.1,
    min = 0,
    x = (i) => pad + (i * (width - pad * 2)) / (usable.length - 1),
    y = (v) => height - pad - ((v - min) * (height - pad * 2)) / (max - min);
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "WER de validação por época");
  [0, 0.5, 1].forEach((r) => {
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", pad);
    line.setAttribute("x2", width - pad);
    line.setAttribute("y1", pad + r * (height - pad * 2));
    line.setAttribute("y2", pad + r * (height - pad * 2));
    line.setAttribute("stroke", "#e2e5dd");
    svg.append(line);
  });
  const path = document.createElementNS(ns, "path");
  path.setAttribute(
    "d",
    usable
      .map((r, i) => `${i ? "L" : "M"}${x(i)},${y(r.validation_wer)}`)
      .join(" "),
  );
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#276c53");
  path.setAttribute("stroke-width", "2");
  svg.append(path);
  const end = document.createElementNS(ns, "circle");
  end.setAttribute("cx", x(usable.length - 1));
  end.setAttribute("cy", y(usable.at(-1).validation_wer));
  end.setAttribute("r", "3");
  end.setAttribute("fill", "#276c53");
  svg.append(end);
  $("curve").classList.remove("curve-empty");
  $("curve").replaceChildren(svg);
}
function updateTable(baseline, candidate) {
  const target = $("speakerResults");
  target.replaceChildren();
  const speakers = Object.keys(baseline?.per_speaker || {});
  for (const speaker of speakers) {
    const b = baseline.per_speaker[speaker],
      c = candidate?.per_speaker?.[speaker],
      row = document.createElement("tr");
    const name = speaker.split(" - ")[0];
    [
      name,
      String(b.examples),
      percent(b.wer),
      percent(c?.wer),
      c
        ? `${c.wer - b.wer > 0 ? "+" : ""}${((c.wer - b.wer) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 2 })} p.p.`
        : "—",
    ].forEach((text, i) => {
      const cell = document.createElement("td");
      cell.textContent = text;
      if (i === 4 && c)
        cell.className =
          c.wer < b.wer ? "better" : c.wer > b.wer ? "worse" : "";
      row.append(cell);
    });
    target.append(row);
  }
}
async function updateStatus() {
  try {
    const status = await fetchJSON("/api/status");
    lastStatus = status;
    if (status.model_labels) {
      const labels = status.model_labels;
      $("baselineLabel").textContent = `01 / ${labels.baseline}`;
      $("candidateLabel").textContent = `02 / ${labels.candidate}`;
      $("baselineTranscriptLabel").textContent = labels.baseline;
      $("candidateTranscriptLabel").textContent = labels.candidate;
      $("baselineTag").textContent = labels.baseline_format;
      if (labels.baseline === "ANTERIOR 2.1")
        $("introText").textContent = "Compare a versão anterior com o novo treinamento usando a mesma gravação.";
    }
    const p = status.progress;
    $("phase").textContent = phaseNames[p.phase] || p.phase;
    $("progressMessage").textContent = p.message || "";
    $("statusDot").className =
      `status-dot ${p.phase === "completed" ? "complete" : p.phase === "failed" ? "failed" : ""}`;
    $("epochNote").textContent =
      p.phase === "completed" && status.training?.epochs_completed
        ? `${status.training.epochs_completed} ÉPOCAS · RTX 3080`
        : p.phase === "caching_encoder"
        ? `${p.completed || 0} / ${p.total || 153} gravações`
        : p.epoch
          ? `ÉPOCA ${p.epoch} / ${p.max_epochs}`
          : "RTX 3080 · 10 GB";
    $("datasetCount").textContent = status.dataset.valid_examples;
    const baseline = status.comparison?.baseline || status.baseline,
      candidate = status.comparison?.candidate;
    $("originalWer").textContent = percent(baseline?.metrics?.wer);
    $("originalCer").textContent = percent(baseline?.metrics?.cer);
    $("candidateWer").textContent = percent(candidate?.metrics?.wer);
    $("candidateCer").textContent = percent(candidate?.metrics?.cer);
    $("testCount").textContent =
      `${baseline?.metrics?.examples || 17} frases de teste`;
    $("candidateTag").textContent = status.candidate_available
      ? `${status.export?.selected_runtime_format || "FP32"} · ${status.export?.publication ? `PADRÃO ${status.export.publication.version}` : "REVISÃO"}`
      : candidate
        ? "AVALIADO"
        : "EM PREPARAÇÃO";
    $("candidateMetricNote").textContent = candidate
      ? "WER no mesmo teste"
      : "Avaliação após treinamento";
    if (status.comparison) {
      const delta = status.comparison.comparison.wer_delta;
      $("delta").textContent =
        `${delta > 0 ? "+" : ""}${(delta * 100).toLocaleString("pt-BR", { maximumFractionDigits: 2 })} pontos percentuais`;
      $("delta").className = delta < 0 ? "better" : delta > 0 ? "worse" : "";
      $("finalNote").textContent = status.comparison.comparison.improved
        ? "Melhoria observada no teste. Produção preservada."
        : delta === 0
          ? "WER igual no teste. Produção preservada."
          : delta < 0
            ? "Menos erros de palavras; houve pequena piora em caracteres. Avalie com novas falas."
            : "WER maior no teste. Produção preservada.";
      if (status.export?.publication)
        $("finalNote").textContent = `Modelo publicado como padrão no Arandu STT ${status.export.publication.version}.`;
    }
    $("bestEpoch").textContent =
      status.training?.best_epoch != null
        ? `MELHOR: ÉPOCA ${status.training.best_epoch}`
        : p.best_epoch != null
          ? `MELHOR: ÉPOCA ${p.best_epoch}`
          : "—";
    if (status.export?.review_checkpoint?.endsWith("final_epoch.nemo")) {
      $("bestEpoch").textContent = `EXIBIDO: ÉPOCA ${status.training.epochs_completed}`;
      $("curveCaption").textContent = `Você está avaliando a época ${status.training.epochs_completed}. A melhor validação interna manteve a versão anterior.`;
    }
    drawCurve(status.history.length ? status.history : p.history || []);
    updateTable(baseline, candidate);
  } catch (error) {
    $("progressMessage").textContent = "Reconectando ao servidor local…";
  }
}
function filteredSamples() {
  return samples.filter(
    (s) =>
      (!$("speakerSelect").value || s.speaker === $("speakerSelect").value) &&
      s.split === $("splitSelect").value,
  );
}
function updateSamples() {
  const select = $("sampleSelect");
  select.replaceChildren();
  filteredSamples().forEach((s, i) => {
    const option = document.createElement("option");
    option.value = s.id;
    option.textContent = `${String(i + 1).padStart(2, "0")} · ${s.text}`;
    select.append(option);
  });
  $("compareButton").disabled = !select.options.length;
  selectSample();
}
function selectSample() {
  const sample = samples.find((s) => s.id === $("sampleSelect").value);
  if (!sample) {
    $("referenceText").textContent = "Nenhuma gravação neste filtro.";
    $("sampleAudio").removeAttribute("src");
    return;
  }
  $("sampleAudio").src = `/api/audio/${encodeURIComponent(sample.id)}`;
  $("audioDuration").textContent =
    `${sample.duration.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} s`;
  $("referenceText").textContent = sample.text;
  $("originalText").textContent =
    "Clique em comparar para reconhecer esta fala.";
  $("adaptedText").textContent = lastStatus?.candidate_available
    ? "Clique em comparar para reconhecer esta fala."
    : "Disponível após o treinamento e a exportação.";
  for (const id of ["originalText", "adaptedText"])
    $(id).classList.add("empty");
  for (const prefix of ["original", "adapted"])
    $(prefix + "Latency").textContent = "—";
  $("originalErrors").textContent = lastStatus?.model_labels
    ? `${lastStatus.model_labels.baseline} · ${lastStatus.model_labels.baseline_format}`
    : "FastConformer CTC original · INT8";
  $("adaptedErrors").textContent = "Encoder original · decoder CTC adaptado";
  showError("");
}
function displayComparison(data) {
  $("referenceText").textContent =
    data.reference ||
    "Sem referência: compare as duas transcrições pela escuta.";
  for (const [key, prefix] of [
    ["original", "original"],
    ["adapted", "adapted"],
  ]) {
    const result = data[key];
    if (!result) {
      $("adaptedText").textContent =
        "O modelo adaptado ainda está em preparação.";
      $("adaptedText").classList.add("empty");
      continue;
    }
    markTranscript($(prefix + "Text"), result.text, data.reference);
    $(prefix + "Latency").textContent =
      `${Math.round(result.latency_ms)} ms · ${result.format}`;
    $(prefix + "Errors").textContent = result.metrics
      ? `${result.metrics.word_errors} erros em ${result.metrics.reference_words} palavras · WER ${percent(result.metrics.wer)} · CER ${percent(result.metrics.cer)}`
      : "Sem referência para calcular WER/CER.";
  }
}
async function compareSample() {
  if (busy) return;
  busy = true;
  showError("");
  const button = $("compareButton");
  button.disabled = true;
  button.textContent = "Reconhecendo…";
  try {
    displayComparison(
      await fetchJSON(
        `/api/compare/${encodeURIComponent($("sampleSelect").value)}`,
      ),
    );
  } catch (error) {
    showError(error.message);
  } finally {
    busy = false;
    button.disabled = false;
    button.textContent = "Comparar gravação ↗";
  }
}
function setUpload(blob) {
  uploadBlob = blob;
  $("uploadCompareButton").disabled = false;
  $("uploadAudio").hidden = false;
  const previous = $("uploadAudio").dataset.objectUrl;
  if (previous) URL.revokeObjectURL(previous);
  const url = URL.createObjectURL(blob);
  $("uploadAudio").dataset.objectUrl = url;
  $("uploadAudio").src = url;
  showError("");
}
async function compareUpload() {
  if (!uploadBlob || busy) return;
  busy = true;
  const button = $("uploadCompareButton");
  button.disabled = true;
  button.textContent = "Reconhecendo…";
  showError("");
  try {
    const query = new URLSearchParams({
      reference: $("uploadReference").value,
    });
    displayComparison(
      await fetchJSON(`/api/compare-upload?${query}`, {
        method: "POST",
        headers: {
          "Content-Type": uploadBlob.type || "application/octet-stream",
        },
        body: uploadBlob,
      }),
    );
  } catch (error) {
    showError(error.message);
  } finally {
    busy = false;
    button.disabled = false;
    button.textContent = "Comparar meu áudio ↗";
  }
}
async function toggleRecord() {
  const button = $("recordButton");
  if (button.disabled || busy) return;
  if (recorder?.state === "recording") {
    button.disabled = true;
    button.textContent = "Preparando áudio…";
    recorder.stop();
    return;
  }
  button.disabled = true;
  showError("");
  $("recordHint").textContent = "Permita o microfone quando o navegador solicitar.";
  let stream = null;
  try {
    if (!navigator.mediaDevices?.getUserMedia || !globalThis.MediaRecorder)
      throw new Error(
        "Abra http://127.0.0.1:8765 no Chrome ou Edge para gravar. Você também pode selecionar um arquivo.",
      );
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordStream = stream;
    const parts = [];
    const mimeType = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"]
      .find((type) => MediaRecorder.isTypeSupported(type));
    const capture = new MediaRecorder(stream, mimeType ? { mimeType } : {});
    recorder = capture;
    let captureFailed = false;
    capture.ondataavailable = (e) => {
      if (e.data.size) parts.push(e.data);
    };
    capture.onerror = () => {
      captureFailed = true;
      stream.getTracks().forEach((t) => t.stop());
      clearTimeout(recordTimeout);
      button.disabled = false;
      button.textContent = "Gravar novamente";
      showError("Falha ao gravar. Tente novamente ou selecione um arquivo de áudio.");
    };
    capture.onstop = async () => {
      clearTimeout(recordTimeout);
      stream.getTracks().forEach((t) => t.stop());
      recorder = null;
      recordStream = null;
      if (captureFailed) return;
      const blob = new Blob(parts, { type: capture.mimeType });
      if (!blob.size) {
        button.disabled = false;
        button.textContent = "Gravar novamente";
        showError("A gravação ficou vazia. Verifique o microfone e tente novamente.");
        return;
      }
      setUpload(blob);
      $("recordHint").textContent =
        "Gravação concluída. Comparando antes e depois do treino…";
      button.disabled = true;
      await compareUpload();
      button.disabled = false;
      button.textContent = "Gravar novamente";
      $("recordHint").textContent = "Confira as duas transcrições abaixo. Seu áudio fica neste computador.";
    };
    capture.start(250);
    $("uploadCompareButton").disabled = true;
    button.disabled = false;
    button.textContent = "Parar e comparar";
    $("recordHint").textContent = "Gravando. Clique em Parar e comparar · limite de 34 segundos.";
    recordTimeout = setTimeout(() => {
      if (capture.state === "recording") {
        button.disabled = true;
        capture.stop();
      }
    }, 34000);
  } catch (error) {
    stream?.getTracks().forEach((t) => t.stop());
    recorder = null;
    recordStream = null;
    button.disabled = false;
    button.textContent = "Gravar no microfone";
    $("recordHint").textContent = "Gravação não iniciada.";
    const messages = {
      NotAllowedError: "Microfone bloqueado. Permita o microfone nas configurações deste site no navegador e tente novamente.",
      NotFoundError: "Nenhum microfone encontrado. Conecte ou selecione um microfone no navegador.",
      NotReadableError: "O navegador não conseguiu abrir o microfone. Verifique se outro aplicativo está usando o dispositivo.",
    };
    showError(messages[error.name] || error.message);
  }
}
$("speakerSelect").addEventListener("change", updateSamples);
$("splitSelect").addEventListener("change", updateSamples);
$("sampleSelect").addEventListener("change", selectSample);
$("compareButton").addEventListener("click", compareSample);
$("nextSample").addEventListener("click", () => {
  const select = $("sampleSelect");
  if (select.options.length) {
    select.selectedIndex = (select.selectedIndex + 1) % select.options.length;
    selectSample();
  }
});
$("uploadCompareButton").addEventListener("click", compareUpload);
$("audioFile").addEventListener("change", () => {
  const file = $("audioFile").files[0];
  if (file) setUpload(file);
});
$("recordButton").addEventListener("click", toggleRecord);
for (const tab of ["dataset", "upload"]) {
  $(tab + "Tab").addEventListener("click", () => {
    for (const other of ["dataset", "upload"]) {
      const active = other === tab;
      $(other + "Tab").classList.toggle("active", active);
      $(other + "Tab").setAttribute("aria-selected", String(active));
      $(other + "Panel").hidden = !active;
    }
    showError("");
  });
}
for (const tab of ["dataset", "upload"]) {
  $(tab + "Tab").addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const other = $(tab === "dataset" ? "uploadTab" : "datasetTab");
      other.focus();
      other.click();
    }
  });
}
(async () => {
  try {
    samples = await fetchJSON("/api/samples");
    [...new Set(samples.map((s) => s.speaker))].sort().forEach((speaker) => {
      const option = document.createElement("option");
      option.value = speaker;
      option.textContent = speaker.split(" - ")[0];
      $("speakerSelect").append(option);
    });
    updateSamples();
    await updateStatus();
    setInterval(updateStatus, 4000);
  } catch (error) {
    showError(error.message);
  }
})();
