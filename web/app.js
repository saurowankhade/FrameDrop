"use strict";

const $ = (id) => document.getElementById(id);

const drop = $("drop");
const fileInput = $("file");
const chosen = $("chosen");
const convertBtn = $("convert");
const progress = $("progress");
const fill = $("fill");
const status = $("status");
const result = $("result");
const download = $("download");
const again = $("again");
const warnList = $("warn");
const errorBox = $("error");
const previewWrap = $("preview-wrap");
const previewImg = $("preview-img");
const previewSpinner = $("preview-spinner");

const OPTION_IDS = [
  "opt-cursor", "opt-zooms", "opt-webcam", "opt-audio",
  "opt-screen", "opt-camera-size", "opt-camera-roundness", "opt-quality",
];

// Map each option control to the query/form field the API expects.
const OPTION_FIELDS = {
  "opt-cursor": "cursor",
  "opt-zooms": "zooms",
  "opt-webcam": "webcam",
  "opt-audio": "audio_cleanup",
  "opt-screen": "screen_size",
  "opt-camera-size": "camera_size",
  "opt-camera-roundness": "camera_roundness",
  "opt-quality": "quality",
};

let selectedFile = null;
let uploadId = null;
let poller = null;
let previewTimer = null;

function currentOptions() {
  const params = new URLSearchParams();
  for (const id of OPTION_IDS) params.append(OPTION_FIELDS[id], $(id).value);
  return params;
}

async function pickFile(f) {
  if (!f) return;
  selectedFile = f;
  uploadId = null;
  chosen.textContent = f.name;
  chosen.hidden = false;
  errorBox.hidden = true;
  convertBtn.disabled = true;
  convertBtn.textContent = "Preparing…";

  // Upload immediately so we can render a live preview as options change.
  const form = new FormData();
  form.append("file", f);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: form });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || "Upload failed.");
    }
    uploadId = (await res.json()).id;
  } catch (err) {
    showError(err.message || "Upload failed.");
    return;
  }

  convertBtn.disabled = false;
  convertBtn.textContent = "Convert to MP4";
  refreshPreview();
}

function refreshPreview() {
  if (!uploadId) return;
  previewWrap.hidden = false;
  previewSpinner.hidden = false;
  const url = `/api/preview/${uploadId}?${currentOptions().toString()}&t=${Date.now()}`;
  const img = new Image();
  img.onload = () => {
    previewImg.src = img.src;
    previewSpinner.hidden = true;
  };
  img.onerror = () => {
    previewSpinner.hidden = true;
  };
  img.src = url;
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, 500);
}

// Re-render the preview shortly after any option changes.
OPTION_IDS.forEach((id) => $(id).addEventListener("change", schedulePreview));

$("pick").addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.click();
});
drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") fileInput.click();
});
fileInput.addEventListener("change", (e) => pickFile(e.target.files[0]));

drop.addEventListener("dragover", (e) => {
  e.preventDefault();
  drop.classList.add("over");
});
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  pickFile(e.dataTransfer.files[0]);
});

function setProgress(frac, msg) {
  progress.hidden = false;
  fill.style.width = Math.round(frac * 100) + "%";
  if (msg) status.textContent = msg;
}

function showError(msg) {
  errorBox.hidden = false;
  errorBox.textContent = msg;
  progress.hidden = true;
  convertBtn.disabled = false;
  convertBtn.hidden = false;
}

function reset() {
  clearInterval(poller);
  clearTimeout(previewTimer);
  selectedFile = null;
  uploadId = null;
  fileInput.value = "";
  chosen.hidden = true;
  convertBtn.hidden = false;
  convertBtn.disabled = true;
  convertBtn.textContent = "Convert to MP4";
  progress.hidden = true;
  result.hidden = true;
  errorBox.hidden = true;
  warnList.innerHTML = "";
  fill.style.width = "0";
  previewWrap.hidden = true;
  previewImg.removeAttribute("src");
}

again.addEventListener("click", reset);

convertBtn.addEventListener("click", async () => {
  if (!uploadId) return;
  convertBtn.disabled = true;
  convertBtn.hidden = true;
  result.hidden = true;
  errorBox.hidden = true;
  setProgress(0.05, "Queued…");

  const form = new FormData();
  form.append("job_id", uploadId);
  for (const [field, value] of currentOptions()) form.append(field, value);

  let jobId;
  try {
    const res = await fetch("/api/convert", { method: "POST", body: form });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || "Could not start the conversion.");
    }
    jobId = (await res.json()).id;
  } catch (err) {
    showError(err.message || "Could not start the conversion.");
    return;
  }

  poller = setInterval(() => pollJob(jobId), 1000);
});

async function pollJob(jobId) {
  let job;
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (!res.ok) throw new Error("Lost track of the job.");
    job = await res.json();
  } catch (err) {
    clearInterval(poller);
    showError(err.message);
    return;
  }

  if (job.state === "error") {
    clearInterval(poller);
    showError(job.error || "Conversion failed.");
    return;
  }

  setProgress(job.progress, job.message + "…");

  if (job.state === "done" && job.download_ready) {
    clearInterval(poller);
    setProgress(1, "Done");
    download.href = `/api/jobs/${jobId}/download`;
    warnList.innerHTML = "";
    (job.warnings || []).forEach((w) => {
      const li = document.createElement("li");
      li.textContent = w;
      warnList.appendChild(li);
    });
    result.hidden = false;
  }
}
