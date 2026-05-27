const state = {
  dashboard: null,
};

const $ = (selector) => document.querySelector(selector);

document.addEventListener("DOMContentLoaded", () => {
  const today = new Date().toISOString().slice(0, 10);
  $("#target-date").value = today;
  $("#dashboard-form").addEventListener("submit", (event) => {
    event.preventDefault();
    loadDashboard($("#target-date").value);
  });
  $("#source-refresh").addEventListener("click", loadSources);
  loadDashboard(today);
});

async function loadDashboard(date) {
  setStatus("Dashboard verisi çekiliyor. Ağır API'ler biraz düşünebilir; normal insan işi değil.", "");
  try {
    const response = await fetch(`/api/dashboard?date=${encodeURIComponent(date)}`);
    if (!response.ok) throw new Error(await response.text());
    state.dashboard = await response.json();
    renderDashboard(state.dashboard);
    setStatus("Canlı dashboard güncellendi.", "ok");
  } catch (error) {
    console.error(error);
    setStatus(`Dashboard yüklenemedi: ${error.message}`, "error");
  }
}

async function loadSources() {
  setStatus("Kaynak health-check çalışıyor.", "");
  try {
    const response = await fetch("/api/sources");
    if (!response.ok) throw new Error(await response.text());
    const payload = await response.json();
    renderResources(payload.resources);
    setStatus("Kaynak matrisi güncellendi.", "ok");
  } catch (error) {
    console.error(error);
    setStatus(`Kaynak testi başarısız: ${error.message}`, "error");
  }
}

function renderDashboard(payload) {
  const summary = payload.summary;
  $("#target-date").value = payload.meta.targetDate;
  $("#final-tmax").textContent = fmtC(summary.finalTmaxC);
  $("#range").textContent = `${fmtC(summary.rangeLowC)} – ${fmtC(summary.rangeHighC)} ana aralık`;
  $("#confidence").textContent = `${summary.confidenceScore}/100`;
  $("#boundary-risk").textContent = `Sınır riski: ${summary.boundaryRisk}`;
  $("#verdict").textContent = summary.verdict;
  $("#edge-summary").textContent = summary.edgeSummary || "Edge yok";
  $("#generated-at").textContent = formatDateTime(payload.meta.generatedAt);
  $("#station-pill").textContent = `${payload.meta.station.icao} · ${payload.meta.station.elevationM}m`;

  renderModels(payload.models, summary);
  drawModelChart(payload.models);
  renderWeather(payload.weather);
  renderMarket(payload.market);
  renderAdjustments(payload.adjustments, payload.risks);
  renderForum(payload.forum);
  renderMapLinks(payload.meta.links);
  renderMethodCards(payload.methodCards);
  renderResources(payload.resources);
  $("#report-text").textContent = payload.reportText || "";
}

function renderModels(models, summary) {
  const availableValues = models.filter((m) => m.tmaxC !== null && m.tmaxC !== undefined).map((m) => m.tmaxC);
  const min = Math.min(...availableValues, summary.rangeLowC ?? 0);
  const max = Math.max(...availableValues, summary.rangeHighC ?? 1);
  $("#model-list").innerHTML = models.map((model) => {
    const pct = model.tmaxC == null || max === min ? 0 : ((model.tmaxC - min) / (max - min)) * 100;
    return `
      <article class="model-card">
        <div class="model-top">
          <strong>${escapeHtml(model.label)}</strong>
          <span>${model.available ? fmtC(model.tmaxC) : "veri yok"}</span>
        </div>
        <div class="bar"><span style="width:${clamp(pct, 3, 100)}%"></span></div>
        <div class="metric-row">
          <span>Ağırlık</span><span>${fmtPct(model.weight, 0)}</span>
        </div>
        <div class="metric-row">
          <span>Peak</span><span>${model.peakTime ? formatTime(model.peakTime) : "—"}</span>
        </div>
        ${model.unavailableReason ? `<p class="muted">${escapeHtml(model.unavailableReason)}</p>` : ""}
      </article>
    `;
  }).join("");
}

function drawModelChart(models) {
  const canvas = $("#model-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "rgba(2, 6, 16, 0.2)";
  ctx.fillRect(0, 0, width, height);

  const series = models
    .map((model, index) => ({
      ...model,
      color: ["#66e4ff", "#a78bfa", "#63e6a5", "#ffd166", "#ff6b8a", "#8bd3ff", "#f7aef8"][index % 7],
      points: (model.hourly || []).filter((p) => p.temperatureC !== null && p.temperatureC !== undefined),
    }))
    .filter((model) => model.points.length > 1);

  if (!series.length) {
    ctx.fillStyle = "#93a4bb";
    ctx.font = "26px sans-serif";
    ctx.fillText("Saatlik model eğrisi yok", 36, 72);
    return;
  }

  const temps = series.flatMap((model) => model.points.map((p) => p.temperatureC));
  const minTemp = Math.floor(Math.min(...temps) - 1);
  const maxTemp = Math.ceil(Math.max(...temps) + 1);
  const allTimes = series.flatMap((model) => model.points.map((p) => new Date(p.time).getTime()));
  const minTime = Math.min(...allTimes);
  const maxTime = Math.max(...allTimes);
  const pad = { left: 54, right: 26, top: 28, bottom: 44 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (time) => pad.left + ((time - minTime) / Math.max(1, maxTime - minTime)) * plotW;
  const y = (temp) => pad.top + (1 - (temp - minTemp) / Math.max(1, maxTemp - minTemp)) * plotH;

  ctx.strokeStyle = "rgba(148, 163, 184, 0.18)";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#93a4bb";
  ctx.font = "18px sans-serif";
  for (let temp = minTemp; temp <= maxTemp; temp += 2) {
    const yy = y(temp);
    ctx.beginPath();
    ctx.moveTo(pad.left, yy);
    ctx.lineTo(width - pad.right, yy);
    ctx.stroke();
    ctx.fillText(`${temp}°`, 8, yy + 6);
  }

  series.forEach((model) => {
    ctx.strokeStyle = model.color;
    ctx.lineWidth = 4;
    ctx.beginPath();
    model.points.forEach((point, idx) => {
      const xx = x(new Date(point.time).getTime());
      const yy = y(point.temperatureC);
      if (idx === 0) ctx.moveTo(xx, yy);
      else ctx.lineTo(xx, yy);
    });
    ctx.stroke();
    const last = model.points[model.points.length - 1];
    ctx.fillStyle = model.color;
    ctx.fillText(model.label, x(new Date(last.time).getTime()) - 100, y(last.temperatureC) - 8);
  });
}

function renderWeather(weather) {
  const metar = weather.metar;
  $("#metar-card").innerHTML = metar ? `
    <div class="metric-row"><span>Son METAR</span><strong>${escapeHtml(metar.rawText || "—")}</strong></div>
    <div class="metric-row"><span>Gözlem</span><span>${formatDateTime(metar.observedAt)} · ${metar.ageMinutes} dk</span></div>
    <div class="metric-row"><span>Sıcaklık / çiğ</span><span>${fmtC(metar.temperatureC)} / ${fmtC(metar.dewPointC)}</span></div>
    <div class="metric-row"><span>Rüzgâr</span><span>${metar.windDirectionDeg ?? "VRB"}° / ${fmtNumber(metar.windSpeedKt)} kt</span></div>
    <div class="metric-row"><span>Basınç</span><span>${fmtNumber(metar.pressureHpa)} hPa</span></div>
  ` : `<div class="metric-row"><span>METAR</span><strong>veri yok</strong></div>`;

  const taf = weather.taf;
  $("#taf-card").innerHTML = taf ? `
    <div class="metric-row"><span>TAF yayın</span><span>${formatDateTime(taf.issuedAt)}</span></div>
    <div class="metric-row"><span>SHRA/TS/CB</span><strong>${taf.rainOrStormRisk ? "risk var" : "belirgin değil"}</strong></div>
    <p class="muted">${escapeHtml(taf.rawText || "")}</p>
  ` : `<div class="metric-row"><span>TAF</span><strong>veri yok</strong></div>`;

  const rows = weather.recentObservations || [];
  $("#observation-strip").innerHTML = rows.map((row) => {
    const height = row.temperatureC == null ? 8 : clamp((row.temperatureC + 10) * 2.1, 12, 84);
    return `<div class="spark"><div class="spark-bar" style="height:${height}px"></div><span>${fmtNumber(row.temperatureC)}</span></div>`;
  }).join("");
}

function renderMarket(market) {
  if (!market) {
    $("#market-summary").innerHTML = `<strong>İlgili market bulunamadı.</strong>`;
    $("#market-table").innerHTML = "";
    $("#market-link").removeAttribute("href");
    return;
  }
  $("#market-link").href = market.link;
  $("#market-summary").innerHTML = `
    <div class="metric-row"><span>${escapeHtml(market.title)}</span><strong>${market.active && !market.closed ? "aktif" : "kapalı"}</strong></div>
    <div class="metric-row"><span>Likidite</span><span>$${fmtNumber(market.liquidity)} · Hacim $${fmtNumber(market.volume)}</span></div>
    <div class="metric-row"><span>Geçerlilik</span><span>${market.validForTarget ? "LTAC hedefe uygun" : escapeHtml(market.validationMessage || "kontrol gerekli")}</span></div>
  `;
  $("#market-table").innerHTML = market.outcomes.map((row) => {
    const edgeClass = (row.edgePp ?? 0) >= 0 ? "edge-pos" : "edge-neg";
    return `
      <tr>
        <td>${escapeHtml(row.bracket)}</td>
        <td>${fmtPct(row.impliedProbability)}</td>
        <td>${fmtPct(row.fairProbability)}</td>
        <td class="${edgeClass}">${row.edgePp == null ? "—" : `${row.edgePp > 0 ? "+" : ""}${row.edgePp}pp`}</td>
        <td>${fmtNumber(row.spread)}</td>
      </tr>
    `;
  }).join("");
}

function renderAdjustments(adjustments, risks) {
  $("#adjustments").innerHTML = adjustments.map((item) => `
    <div class="adjustment">
      <strong>${escapeHtml(item.label)}</strong>
      <span class="delta">${item.valueC > 0 ? "+" : ""}${fmtNumber(item.valueC)}°</span>
      <span class="muted">${escapeHtml(item.summary || "")}</span>
    </div>
  `).join("");
  $("#risks").innerHTML = `
    <div class="metric-row"><span>Yukarı risk</span><span>${escapeHtml(risks.upward || "—")}</span></div>
    <div class="metric-row"><span>Aşağı risk</span><span>${escapeHtml(risks.downward || "—")}</span></div>
    <div class="metric-row"><span>Kritik</span><strong>${escapeHtml(risks.critical || "—")}</strong></div>
  `;
}

function renderForum(forum) {
  $("#forum").innerHTML = forum ? `
    <div class="metric-row"><span>Mesaj</span><strong>${forum.postCount}</strong></div>
    <div class="metric-row"><span>Bölgeler</span><span>${escapeHtml((forum.locations || []).join(", ") || "—")}</span></div>
    <p class="muted">${escapeHtml(forum.summary || forum.unavailableReason || "veri yok")}</p>
  ` : `<p class="muted">Forum verisi yok.</p>`;
}

function renderMapLinks(links) {
  const items = [
    ["Windy uydu", links.windySatellite],
    ["Windy radar", links.windyRadar],
    ["Polymarket", links.polymarket],
    ["HavaForum", links.havaforum],
  ].filter(([, href]) => href);
  $("#map-links").innerHTML = items.map(([label, href]) => `
    <a class="link-card" href="${escapeAttr(href)}" target="_blank" rel="noreferrer">
      <strong>${escapeHtml(label)}</strong>
      <p>${escapeHtml(href)}</p>
    </a>
  `).join("");
}

function renderMethodCards(cards) {
  $("#method-cards").innerHTML = cards.map((card) => `
    <article class="method-card">
      <strong>${escapeHtml(card.title)}</strong>
      <p>${escapeHtml(card.body)}</p>
    </article>
  `).join("");
}

function renderResources(resources) {
  $("#resources").innerHTML = resources.map((item) => `
    <article class="resource">
      <div>
        <strong>${escapeHtml(item.name)}</strong>
        <p>${escapeHtml(item.label)} · ${escapeHtml(item.role)}</p>
        <p>${item.env ? escapeHtml(item.env) : "key gerektirmez"}${item.latencyMs ? ` · ${item.latencyMs} ms` : ""}${item.message ? ` · ${escapeHtml(item.message)}` : ""}</p>
      </div>
      <span class="state ${escapeAttr(item.state)}">${escapeHtml(item.state)}</span>
    </article>
  `).join("");
}

function setStatus(text, kind) {
  const node = $("#status");
  node.textContent = text;
  node.className = `status ${kind || ""}`;
}

function fmtC(value) {
  return value === null || value === undefined ? "—" : `${Number(value).toFixed(1)}°C`;
}

function fmtNumber(value, digits = 1) {
  return value === null || value === undefined || Number.isNaN(Number(value)) ? "—" : Number(value).toFixed(digits);
}

function fmtPct(value, digits = 1) {
  return value === null || value === undefined ? "—" : `${(Number(value) * 100).toFixed(digits)}%`;
}

function formatDateTime(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("tr-TR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatTime(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("tr-TR", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
