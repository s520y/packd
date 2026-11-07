const MIN_SECONDS_BETWEEN_SCANS = 2;
const MAX_IMAGE_EDGE = 1280; // Raise this if small items are missed.
const MAX_RECEIPT_EDGE = 1568; // Receipts need more detail so the text stays readable.
const JPEG_QUALITY = 0.8;
const TIMER_WARN_SECONDS = 120;
const TIMER_LATE_SECONDS = 240;

const DISPLAY_REFRESH_MS = 1000;
const displayOnly = new URLSearchParams(location.search).has("display");

const el = (id) => document.getElementById(id);
const video = el("video");
const canvas = el("canvas");

let scanning = true;
let mockMode = false;
let mockCounts = {};
let mockOrderKey = null; // Rebuilds the test panel when the order changes.
let hasOrder = false;
let reviewing = false; // Pauses tray scans while an order is being read or checked.
let timerBase = null; // { seconds, receivedAt }
let scanNumber = 0; // A scan that started before "New tray" must not update the screen.

function missingText(lines) {
  return lines
    .filter((line) => line.missing > 0)
    .map((line) => (line.missing > 1 ? `${line.item} x${line.missing}` : line.item))
    .join(", ");
}

function statusText(state) {
  const missing = missingText(state.lines);
  const tooMany = state.lines.filter((line) => line.too_many > 0).map((line) => `too many ${line.item}`);
  const wrong = state.extra_items.map((item) => `remove ${item}`);
  const detail = [missing && `Missing: ${missing}`, ...tooMany, ...wrong].filter(Boolean).join(" · ");

  switch (state.status) {
    case "complete": return ["Tray complete", "Ready to hand off"];
    case "incomplete": return ["Tray incomplete", detail];
    case "packing": return ["Packing", detail];
    case "no_order": return ["Scan a receipt to start", "Use a photo of the receipt or a ticket screenshot"];
    default: return ["Waiting for first item", "Place items on the tray"];
  }
}

function render(state) {
  hasOrder = state.status !== "no_order";
  const [title, detail] = statusText(state);
  el("status").className = `status status-${state.status}`;
  el("status-title").textContent = title;
  el("status-detail").textContent = detail;
  el("order-name").textContent = state.order_name;

  const list = el("checklist");
  list.replaceChildren();
  for (const line of state.lines) {
    const row = document.createElement("li");
    if (line.too_many > 0) row.className = "problem";
    else if (line.missing === 0) row.className = "done";
    const name = document.createElement("span");
    name.textContent = line.item;
    if (line.note) {
      const note = document.createElement("small");
      note.className = "item-note";
      note.textContent = `Check by hand: ${line.note}`;
      name.append(note);
    }
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `${line.have} of ${line.qty}`;
    row.append(name, count);
    list.append(row);
  }
  for (const item of state.extra_items) {
    const row = document.createElement("li");
    row.className = "wrong";
    row.textContent = `Not in order: ${item}`;
    list.append(row);
  }

  timerBase = state.elapsed_seconds === null
    ? null
    : { seconds: state.elapsed_seconds, receivedAt: performance.now() };
  renderTimer();

  const orderKey = state.lines.map((line) => line.item).join("\n");
  if (mockMode && orderKey !== mockOrderKey) {
    mockOrderKey = orderKey;
    buildMockPanel(state.lines);
  }
}

function renderTimer() {
  const timer = el("timer");
  const seconds = timerBase
    ? Math.floor(timerBase.seconds + (performance.now() - timerBase.receivedAt) / 1000)
    : 0;
  timer.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  timer.classList.toggle("warn", seconds >= TIMER_WARN_SECONDS && seconds < TIMER_LATE_SECONDS);
  timer.classList.toggle("late", seconds >= TIMER_LATE_SECONDS);
}

function captureFrame() {
  if (!video.videoWidth) return Promise.resolve(null);
  const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(video.videoWidth, video.videoHeight));
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
}

function setScanNote(message, isError = false) {
  const note = el("scan-note");
  note.textContent = message;
  note.classList.toggle("error", isError);
}

async function scanOnce() {
  if (!hasOrder || reviewing) return;
  const frame = await captureFrame();
  if (!frame && !mockMode) {
    setScanNote("Waiting for the camera.");
    return;
  }
  const thisScan = ++scanNumber;
  const started = performance.now();
  try {
    const response = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body: frame ?? new Blob(),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Scan failed.");
    if (thisScan !== scanNumber) return;
    render(body);
    const seconds = ((performance.now() - started) / 1000).toFixed(1);
    const dishes = Object.keys(body.food_hints ?? {}).join(", ");
    setScanNote(body.scan_skipped
      ? "Hand over tray, waiting for it to clear."
      : `Last scan took ${seconds}s.${dishes ? ` Food ID: ${dishes}.` : ""}`);
  } catch (error) {
    setScanNote(`${error.message} Trying again.`, true);
  }
}

async function scanLoop() {
  while (true) {
    const started = performance.now();
    if (scanning) await scanOnce();
    const elapsed = performance.now() - started;
    await new Promise((resolve) =>
      setTimeout(resolve, Math.max(250, MIN_SECONDS_BETWEEN_SCANS * 1000 - elapsed)));
  }
}

async function startCamera(deviceId) {
  const video_options = deviceId
    ? { deviceId: { exact: deviceId } }
    : { facingMode: { ideal: "environment" } };
  video.srcObject?.getTracks().forEach((track) => track.stop());
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { ...video_options, width: { ideal: 1920 }, height: { ideal: 1080 } },
  });
  video.srcObject = stream;
  el("camera-note").hidden = true;
  return stream.getVideoTracks()[0].getSettings().deviceId;
}

async function listCameras(selectedId) {
  const select = el("camera-select");
  const cameras = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  select.replaceChildren();
  cameras.forEach((camera, index) => {
    const option = new Option(camera.label || `Camera ${index + 1}`, camera.deviceId);
    select.add(option);
  });
  if (selectedId) select.value = selectedId;
}

async function setUpCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    el("camera-note").textContent = "This browser blocks the camera here. Open the page on localhost or HTTPS.";
    return;
  }
  try {
    await listCameras(await startCamera());
  } catch (error) {
    el("camera-note").textContent = `Camera unavailable: ${error.message}`;
  }
}

function sendMockTray() {
  return fetch("/api/mock", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      counts: mockCounts,
      extra_items: el("mock-extra").checked ? ["wrong sauce"] : [],
      hands_over_tray: el("mock-hand").checked,
    }),
  });
}

function buildMockPanel(lines) {
  el("mock-panel").hidden = lines.length === 0;
  mockCounts = {};
  const container = el("mock-items");
  container.replaceChildren();
  for (const line of lines) {
    mockCounts[line.item] = 0;
    const row = document.createElement("div");
    row.className = "mock-row";
    const name = document.createElement("span");
    name.textContent = line.item;
    const stepper = document.createElement("div");
    stepper.className = "stepper";
    const value = document.createElement("span");
    value.textContent = "0";
    const change = (delta) => {
      mockCounts[line.item] = Math.max(0, mockCounts[line.item] + delta);
      value.textContent = mockCounts[line.item];
      sendMockTray();
    };
    const button = (label, ariaLabel, delta) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.setAttribute("aria-label", ariaLabel);
      b.onclick = () => change(delta);
      return b;
    };
    stepper.append(
      button("−", `Remove ${line.item}`, -1),
      value,
      button("+", `Add ${line.item}`, 1),
    );
    row.append(name, stepper);
    container.append(row);
  }
  el("mock-extra").onchange = sendMockTray;
  el("mock-hand").onchange = sendMockTray;
}

el("camera-select").onchange = (event) => {
  startCamera(event.target.value).catch((error) => setScanNote(`Camera error: ${error.message}`, true));
};

el("scan-toggle").onclick = (event) => {
  scanning = !scanning;
  event.target.textContent = scanning ? "Pause scanning" : "Resume scanning";
  setScanNote(scanning ? "" : "Scanning paused.");
};

el("reset").onclick = async () => {
  scanNumber += 1;
  const response = await fetch("/api/reset", { method: "POST" });
  render(await response.json());
  setScanNote("New tray started.");
};

async function shrinkImage(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_RECEIPT_EDGE / Math.max(bitmap.width, bitmap.height));
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
}

function addReviewRow(line = {}) {
  const row = document.createElement("div");
  row.className = "review-row";
  const field = (label, name, type, value, className = "") => {
    const wrapper = document.createElement("label");
    wrapper.className = `field ${className}`;
    wrapper.textContent = label;
    const input = document.createElement("input");
    input.type = type;
    input.name = name;
    input.value = value;
    if (type === "number") {
      input.min = 1;
      input.inputMode = "numeric";
    }
    wrapper.append(input);
    return wrapper;
  };
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "Remove";
  remove.onclick = () => row.remove();
  row.append(
    field("Item", "item", "text", line.item ?? ""),
    field("Qty", "qty", "number", line.qty ?? 1),
    remove,
    field("Looks like", "looks_like", "text", line.looks_like ?? "", "wide"),
    field("Note", "note", "text", line.note ?? "", "wide"),
  );
  el("review-items").append(row);
  return row;
}

function openReview(order) {
  reviewing = true;
  el("review-name").value = order.order_name;
  el("review-items").replaceChildren();
  el("review-error").textContent = "";
  order.items.forEach(addReviewRow);
  if (!order.items.length) addReviewRow();
  el("review").showModal();
}

function closeReview() {
  reviewing = false;
  el("review").close();
}

async function readReceipt(file) {
  const button = el("scan-receipt");
  reviewing = true;
  button.disabled = true;
  setScanNote("Reading the receipt...");
  try {
    const image = await shrinkImage(file);
    const response = await fetch("/api/order/read", {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body: image,
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Couldn't read the receipt.");
    setScanNote("");
    openReview(body);
  } catch (error) {
    reviewing = false;
    setScanNote(`${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

// Some phones stop the live camera while the photo picker is open.
async function resumeCamera() {
  const track = video.srcObject?.getVideoTracks()[0];
  if (track?.readyState === "ended") await startCamera(el("camera-select").value);
  else video.play?.().catch(() => {});
}

el("scan-receipt").onclick = () => el("receipt-file").click();

el("receipt-file").onchange = async (event) => {
  const [file] = event.target.files;
  event.target.value = "";
  if (!file) return;
  if (!displayOnly) resumeCamera().catch(() => {});
  await readReceipt(file);
};

el("type-order").onclick = () => openReview({ order_name: "", items: [] });
el("review-add").onclick = () => addReviewRow().querySelector("input").focus();
el("review-cancel").onclick = closeReview;
el("review").addEventListener("cancel", () => { reviewing = false; });

el("review-form").onsubmit = async (event) => {
  event.preventDefault();
  const items = [...el("review-items").children].map((row) => ({
    item: row.querySelector('[name="item"]').value,
    qty: Number(row.querySelector('[name="qty"]').value) || 1,
    looks_like: row.querySelector('[name="looks_like"]').value,
    note: row.querySelector('[name="note"]').value,
  }));
  const response = await fetch("/api/order", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order_name: el("review-name").value, items }),
  });
  const body = await response.json();
  if (!response.ok) {
    el("review-error").textContent = typeof body.detail === "string" ? body.detail : "Check the item details.";
    return;
  }
  closeReview();
  scanNumber += 1;
  render(body);
  setScanNote("Order loaded. Start packing.");
};

async function start() {
  const state = await (await fetch("/api/state")).json();
  mockMode = state.mock_mode;
  el("test-banner").hidden = !mockMode;
  el("review-test-note").hidden = !mockMode;
  render(state);
  setInterval(renderTimer, 250);
  if (displayOnly) {
    document.body.classList.add("display-only");
    setInterval(refreshDisplay, DISPLAY_REFRESH_MS);
    return;
  }
  setUpCamera();
  keepScreenAwake();
  document.addEventListener("visibilitychange", keepScreenAwake);
  scanLoop();
}

async function refreshDisplay() {
  try {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error("Lost connection to the app.");
    render(await response.json());
    setScanNote("");
  } catch (error) {
    setScanNote(`${error.message} Trying again.`, true);
  }
}

// Stops a mounted phone from locking in the middle of a demo.
async function keepScreenAwake() {
  if (document.visibilityState !== "visible") return;
  try {
    await navigator.wakeLock?.request("screen");
  } catch {
    // Not supported, or denied. Scanning still works.
  }
}

start();
