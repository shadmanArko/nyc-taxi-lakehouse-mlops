// NYC Taxi Trip Time: tiny client for the prediction API (same origin, via /api/).
"use strict";

const $ = (id) => document.getElementById(id);
const form = $("trip");
const pickup = $("pickup");
const dropoff = $("dropoff");
const zoneNames = new Map();

function nycNowForInput() {
  // "YYYY-MM-DDTHH:MM" in New York local time, whatever the visitor's own timezone is.
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date()).map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function fillZones(select, zones, selectedId) {
  select.replaceChildren();
  const byBorough = new Map();
  for (const z of zones) {
    if (!byBorough.has(z.borough)) byBorough.set(z.borough, []);
    byBorough.get(z.borough).push(z);
  }
  for (const [borough, list] of byBorough) {
    const group = document.createElement("optgroup");
    group.label = borough;
    for (const z of list) {
      const option = new Option(z.zone, String(z.id), false, z.id === selectedId);
      group.append(option);
    }
    select.append(group);
  }
  select.disabled = false;
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

async function loadZones() {
  try {
    const response = await fetch("/api/zones");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const zones = await response.json();
    for (const z of zones) zoneNames.set(z.id, `${z.zone} (${z.borough})`);
    fillZones(pickup, zones, 161);   // Midtown Center
    fillZones(dropoff, zones, 237);  // Upper East Side South
    $("go").disabled = false;
  } catch (err) {
    showError("Could not load the list of zones. Please refresh the page.");
  }
}

async function checkModel() {
  try {
    const response = await fetch("/api/ready");
    if (response.ok) {
      const ready = await response.json();
      $("status").textContent = `Model ${ready.model_name} v${ready.model_version} is live`;
    } else {
      $("status").textContent = "The model is loading…";
      setTimeout(checkModel, 5000);
    }
  } catch {
    $("status").textContent = "The service is unreachable";
  }
}

function explain(status, body) {
  if (status === 422) {
    const fields = (body.detail || []).map((d) => d.loc?.at(-1)).filter(Boolean);
    return `Please check: ${[...new Set(fields)].join(", ") || "the inputs"}.`;
  }
  if (status === 429) return "Too many requests. Please wait a moment.";
  if (status === 503) return "The model is still loading. Try again in a few seconds.";
  return "Something went wrong. Please try again.";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  const payload = {
    pickup_location_id: Number(pickup.value),
    dropoff_location_id: Number(dropoff.value),
    trip_distance: Number($("distance").value),
    pickup_datetime: `${$("when").value}:00`,
  };
  $("go").disabled = true;
  $("go").textContent = "Estimating…";
  try {
    const response = await fetch("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      showError(explain(response.status, body));
      return;
    }
    $("minutes").textContent = Math.round(body.predicted_duration_minutes);
    $("route").textContent =
      `${zoneNames.get(payload.pickup_location_id)} → ${zoneNames.get(payload.dropoff_location_id)}`;
    $("lineage").textContent = `Model v${body.model_version} · contract ${body.contract_version}`;
    $("result").hidden = false;
  } catch {
    showError("The service is unreachable. Please try again.");
  } finally {
    $("go").disabled = false;
    $("go").textContent = "Estimate ride time";
  }
});

$("swap").addEventListener("click", () => {
  [pickup.value, dropoff.value] = [dropoff.value, pickup.value];
});

$("when").value = nycNowForInput();
loadZones();
checkModel();
