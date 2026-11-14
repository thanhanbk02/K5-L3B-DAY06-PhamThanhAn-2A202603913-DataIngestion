const pdfjsLib = window.pdfjsLib;
pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdf.worker.min.js";

const messages = document.querySelector("#messages");
const form = document.querySelector("#ask-form");
const questionBox = document.querySelector("#question");
const sendButton = document.querySelector("#send");
const strategyBox = document.querySelector("#strategy");
const canvas = document.querySelector("#pdf-canvas");
const highlightLayer = document.querySelector("#highlight-layer");
const pageLabel = document.querySelector("#page-label");
const evidenceLabel = document.querySelector("#evidence-label");

let pdfDoc = null;
let currentPage = 1;
let activeEvidence = null;

pdfjsLib.getDocument("/api/pdf").promise.then((doc) => {
  pdfDoc = doc;
  return renderPage(1, null);
}).catch((error) => {
  evidenceLabel.textContent = error.message;
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  ask(questionBox.value);
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    questionBox.value = button.dataset.question;
    ask(button.dataset.question);
  });
});

document.querySelector("#prev-page").addEventListener("click", () => {
  if (currentPage > 1) renderPage(currentPage - 1, activeEvidence);
});

document.querySelector("#next-page").addEventListener("click", () => {
  if (pdfDoc && currentPage < pdfDoc.numPages) renderPage(currentPage + 1, activeEvidence);
});

async function ask(question) {
  const text = question.trim();
  if (!text) return;
  appendMessage("user", text);
  sendButton.disabled = true;
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text, strategy: strategyBox.value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Không trả lời được");
    appendAnswer(data);
    activeEvidence = { page: data.page, text: data.evidence, top3: data.top3 };
    await renderPage(data.page, activeEvidence);
  } catch (error) {
    appendMessage("bot", error.message);
  } finally {
    sendButton.disabled = false;
  }
}

function appendMessage(role, text) {
  const node = document.createElement("div");
  node.className = `bubble ${role}`;
  node.textContent = text;
  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function appendAnswer(data) {
  const node = appendMessage("bot", data.answer);
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = `Chiến lược ${data.strategy} · điểm gần đúng ${data.score} · trang ${data.page}`;
  node.appendChild(meta);
  if (!data.top3 || !data.top3.length) return;
  const citations = document.createElement("div");
  citations.className = "citations";
  data.top3.forEach((hit) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `#${hit.rank} trang ${hit.page}`;
    button.addEventListener("click", () => {
      activeEvidence = { page: hit.page, text: hit.text, top3: data.top3 };
      renderPage(hit.page, activeEvidence);
    });
    citations.appendChild(button);
  });
  node.appendChild(citations);
}

async function renderPage(pageNumber, evidence) {
  if (!pdfDoc) return;
  currentPage = pageNumber;
  pageLabel.textContent = `Trang ${pageNumber} / ${pdfDoc.numPages}`;
  const page = await pdfDoc.getPage(pageNumber);
  const stage = document.querySelector("#stage");
  const scale = Math.min(1.6, (stage.clientWidth - 32) / page.getViewport({ scale: 1 }).width);
  const viewport = page.getViewport({ scale });
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  highlightLayer.style.width = `${viewport.width}px`;
  highlightLayer.style.height = `${viewport.height}px`;
  await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
  highlightLayer.replaceChildren();
  const onThisPage = evidence && evidence.page === pageNumber && evidence.text;
  evidenceLabel.textContent = onThisPage
    ? `Bằng chứng bôi vàng ở trang ${pageNumber}`
    : "Trang này không phải trang bằng chứng đang chọn";
  if (!onThisPage) return;
  const textContent = await page.getTextContent();
  const boxes = locateQuote(textContent, viewport, evidence.text);
  boxes.forEach((box) => {
    const mark = document.createElement("div");
    mark.className = "mark";
    mark.style.left = `${box.left}px`;
    mark.style.top = `${box.top}px`;
    mark.style.width = `${box.width}px`;
    mark.style.height = `${box.height}px`;
    highlightLayer.appendChild(mark);
  });
  const first = highlightLayer.querySelector(".mark");
  if (first) first.scrollIntoView({ block: "center", inline: "nearest" });
}

function locateQuote(textContent, viewport, quote) {
  const items = textContent.items.filter((item) => item.str);
  const chars = [];
  let flat = "";
  items.forEach((item, itemIndex) => {
    for (const ch of item.str.normalize("NFC")) {
      if (/\s/u.test(ch)) continue;
      flat += ch.toLocaleLowerCase("vi");
      chars.push(itemIndex);
    }
  });
  const needle = quote.normalize("NFC").replace(/\s+/gu, "").toLocaleLowerCase("vi");
  let start = needle.length >= 24 ? flat.indexOf(needle) : -1;
  let length = needle.length;
  if (start < 0) {
    const anchor = longestAnchor(needle, flat);
    start = anchor.start;
    length = anchor.length;
  }
  if (start < 0 || length < 12) return [];
  const chosen = new Set();
  for (let index = start; index < start + length && index < chars.length; index += 1) {
    chosen.add(chars[index]);
  }
  return [...chosen].map((itemIndex) => boxForItem(items[itemIndex], viewport));
}

function longestAnchor(needle, flat) {
  const size = Math.min(48, needle.length);
  if (size < 12) return { start: -1, length: 0 };
  for (let offset = 0; offset + size <= needle.length; offset += 12) {
    const slice = needle.slice(offset, offset + size);
    const found = flat.indexOf(slice);
    if (found >= 0) return { start: found, length: slice.length };
  }
  return { start: -1, length: 0 };
}

const askView = document.querySelector("#ask-view");
const benchmarkView = document.querySelector("#benchmark-view");
const benchmarkRoot = document.querySelector("#benchmark-root");
const strategyWrap = document.querySelector("#strategy-wrap");
let benchmarkLoaded = false;

document.querySelector("#tab-ask").addEventListener("click", () => showTab("ask"));
document.querySelector("#tab-benchmark").addEventListener("click", () => showTab("benchmark"));

function showTab(name) {
  const askOn = name === "ask";
  askView.hidden = !askOn;
  benchmarkView.hidden = askOn;
  strategyWrap.hidden = !askOn;
  document.querySelector("#tab-ask").classList.toggle("active", askOn);
  document.querySelector("#tab-benchmark").classList.toggle("active", !askOn);
  document.querySelector("#tab-ask").setAttribute("aria-selected", String(askOn));
  document.querySelector("#tab-benchmark").setAttribute("aria-selected", String(!askOn));
  if (!askOn && !benchmarkLoaded) loadBenchmark();
}

async function loadBenchmark() {
  benchmarkLoaded = true;
  try {
    const response = await fetch("/api/benchmark");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Không đọc được benchmark");
    renderBenchmark(data);
  } catch (error) {
    benchmarkLoaded = false;
    benchmarkRoot.textContent = error.message;
  }
}

function renderBenchmark(data) {
  benchmarkRoot.replaceChildren();
  const lead = document.createElement("div");
  lead.className = "benchmark-lead";
  lead.innerHTML = `
    <p class="locked">Cấu hình đã khóa: ${escapeHtml(data.selected_strategy)}</p>
    <h2>Chỉ số chọn trên tập dev, rồi mới chấm tập test</h2>
    <p class="rule">${escapeHtml(data.selection_rule)}</p>
    <p class="note">${escapeHtml(data.test_note.miss_reason)}</p>
  `;
  benchmarkRoot.appendChild(lead);

  const metrics = document.createElement("div");
  metrics.className = "metric-grid";
  data.metrics.forEach((metric) => {
    const card = document.createElement("article");
    card.className = "metric-card";
    card.innerHTML = `<h3>${escapeHtml(metric.name)}</h3><p>${escapeHtml(metric.definition)}</p>`;
    metrics.appendChild(card);
  });
  benchmarkRoot.appendChild(metrics);

  const facts = document.createElement("div");
  facts.className = "fact-grid";
  [
    ["Mô hình", `${data.model} · ${data.dimensions} chiều`],
    ["Tìm kiếm", "Cosine chính xác, không ANN"],
    ["Thời gian embed", `${data.embed_seconds} giây · ${data.cost_usd} USD`],
    ["Trang nội dung", String(data.content_pages)],
  ].forEach(([label, value]) => {
    const fact = document.createElement("div");
    fact.className = "fact";
    fact.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>`;
    facts.appendChild(fact);
  });
  benchmarkRoot.appendChild(facts);

  benchmarkRoot.appendChild(scorePanel(data));
  benchmarkRoot.appendChild(manualPanel(data));
  benchmarkRoot.appendChild(questionPanel(data));
}

function scorePanel(data) {
  const panel = document.createElement("section");
  panel.className = "panel";
  panel.innerHTML = `<h2>So sánh A, B, C</h2>`;
  const table = document.createElement("table");
  table.className = "score-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th>Chiến lược</th><th>Chunk</th>
        <th>Dev Hit@1</th><th>Dev Evidence Hit@3</th>
        <th>Test Hit@1</th><th>Test Evidence Hit@3</th>
      </tr>
    </thead>`;
  const body = document.createElement("tbody");
  ["A", "B", "C"].forEach((name) => {
    const block = data.by_strategy[name];
    const spec = data.strategies[name];
    const row = document.createElement("tr");
    if (name === data.selected_strategy) row.className = "selected";
    const label = name === data.selected_strategy ? `${name} · đã khóa` : name;
    row.innerHTML = `
      <td>${escapeHtml(label)}<br><span class="note">${escapeHtml(chunkNote(spec))}</span></td>
      <td>${spec.chunks}</td>
      <td>${meter(block.dev.hit_at_1)}</td>
      <td>${meter(block.dev.evidence_hit_at_3)}</td>
      <td>${meter(block.test.hit_at_1)}</td>
      <td>${meter(block.test.evidence_hit_at_3)}</td>`;
    body.appendChild(row);
  });
  table.appendChild(body);
  panel.appendChild(table);
  return panel;
}

function chunkNote(spec) {
  const size = spec.size || spec.max_size;
  const overlap = spec.overlap ? `, chồng ${spec.overlap}` : "";
  const filter = spec.filter && spec.filter !== false ? ", có lọc mục" : "";
  return `${spec.chunking} ${size}${overlap}${filter}`;
}

function meter(fraction) {
  const [hit, total] = fraction.split("/").map(Number);
  const pct = total ? Math.round((100 * hit) / total) : 0;
  return `<div class="score"><div class="meter" aria-hidden="true"><span style="width:${pct}%"></span></div><strong>${escapeHtml(fraction)}</strong></div>`;
}

function manualPanel(data) {
  const panel = document.createElement("section");
  panel.className = "panel";
  panel.innerHTML = `<h2>Chấm tay trên cùng vị trí</h2>`;
  const grid = document.createElement("div");
  grid.className = "manual-grid";
  grid.appendChild(manualCard(
    "Mạch lạc / 2",
    data.coherence.scale,
    ["A", "B", "C"].map((name) => `${name}: ${data.coherence.mean_over_2[name]} (${data.coherence.totals[name]})`).join(" · "),
  ));
  grid.appendChild(manualCard(
    "Metadata",
    data.metadata.checked,
    `Đúng và đủ: ${data.metadata.correct_and_sufficient}. Top-3 tuân filter: A ${data.metadata.top3_obeys_filter.A}, B ${data.metadata.top3_obeys_filter.B}, C ${data.metadata.top3_obeys_filter.C}.`,
  ));
  grid.appendChild(manualCard(
    "Grounding / 2",
    data.grounding.scale,
    ["A", "B", "C"].map((name) => `${name}: top-3 ${data.grounding.mean_over_2[name]}, hạng 1 ${data.grounding.rank1_mean_over_2[name]}`).join(" · "),
  ));
  panel.appendChild(grid);

  const filter = document.createElement("table");
  filter.className = "mini-table";
  filter.innerHTML = `<thead><tr><th>Câu có filter</th><th>Mục bắt buộc</th><th>Trang A</th><th>Trang B</th><th>Trang C</th></tr></thead>`;
  const body = document.createElement("tbody");
  Object.entries(data.metadata.filter_detail).forEach(([id, detail]) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${escapeHtml(id)}</td><td>${escapeHtml(detail.required_section.replaceAll("_", " "))}</td><td>${detail.A.join(", ")}</td><td>${detail.B.join(", ")}</td><td>${detail.C.join(", ")}</td>`;
    body.appendChild(row);
  });
  filter.appendChild(body);
  panel.appendChild(filter);
  const note = document.createElement("p");
  note.className = "note";
  note.textContent = data.metadata.useful_field;
  panel.appendChild(note);
  return panel;
}

function manualCard(title, scale, value) {
  const card = document.createElement("article");
  card.className = "metric-card";
  card.innerHTML = `<h3>${escapeHtml(title)}</h3><p>${escapeHtml(value)}</p><p class="scale">${escapeHtml(scale)}</p>`;
  return card;
}

function questionPanel(data) {
  const panel = document.createElement("section");
  panel.className = "panel";
  panel.innerHTML = `<h2>Từng câu hỏi</h2><p class="note">H1 là Hit@1, H3 là Evidence Hit@3. Nút «Hỏi» mở câu đó ở tab Hỏi đáp với chiến lược đang chọn.</p>`;
  const filters = document.createElement("div");
  filters.className = "filters";
  const table = document.createElement("table");
  table.className = "question-table";
  table.innerHTML = `<thead><tr><th>Câu</th><th>Tập</th><th>Trang</th><th>A</th><th>B</th><th>C</th></tr></thead>`;
  const body = document.createElement("tbody");
  data.questions.forEach((item) => {
    const row = document.createElement("tr");
    row.dataset.split = item.split;
    const questionCell = document.createElement("td");
    questionCell.className = "q";
    questionCell.textContent = `${item.id}. ${item.question}`;
    const open = document.createElement("button");
    open.type = "button";
    open.className = "ask-link";
    open.textContent = "Hỏi";
    open.addEventListener("click", () => {
      showTab("ask");
      questionBox.value = item.question;
      ask(item.question);
    });
    questionCell.appendChild(document.createElement("br"));
    questionCell.appendChild(open);
    row.appendChild(questionCell);
    row.insertAdjacentHTML("beforeend", `<td>${item.split}</td><td>${item.gold_page}</td>`);
    ["A", "B", "C"].forEach((name) => {
      const cell = document.createElement("td");
      cell.innerHTML = pair(item.strategies[name]);
      row.appendChild(cell);
    });
    body.appendChild(row);
  });
  table.appendChild(body);
  [
    ["all", "Tất cả"],
    ["dev", "Dev"],
    ["test", "Test"],
  ].forEach(([value, label], index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    if (index === 0) button.className = "active";
    button.addEventListener("click", () => {
      filters.querySelectorAll("button").forEach((node) => node.classList.remove("active"));
      button.classList.add("active");
      body.querySelectorAll("tr").forEach((row) => {
        row.hidden = value !== "all" && row.dataset.split !== value;
      });
    });
    filters.appendChild(button);
  });
  panel.appendChild(filters);
  panel.appendChild(table);
  return panel;
}

function pair(result) {
  if (!result) return "";
  return `${pill("H1", result.hit_at_1)}${pill("H3", result.evidence_hit_at_3)}`;
}

function pill(label, hit) {
  return `<span class="pill ${hit ? "hit" : "miss"}">${label} ${hit ? "đúng" : "trượt"}</span>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function boxForItem(item, viewport) {
  const transform = pdfjsLib.Util.transform(viewport.transform, item.transform);
  const height = Math.hypot(transform[2], transform[3]);
  return {
    left: transform[4],
    top: transform[5] - height,
    width: Math.max(item.width * viewport.scale, 4),
    height: Math.max(height, 8),
  };
}
