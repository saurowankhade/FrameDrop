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

let selectedFile = null;
let poller = null;

function pickFile(f) {
  if (!f) return;
  selectedFile = f;
  chosen.textContent = f.name;
  chosen.hidden = false;
  convertBtn.disabled = false;
  errorBox.hidden = true;
}

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
  selectedFile = null;
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
}

again.addEventListener("click", reset);

convertBtn.addEventListener("click", async () => {
  if (!selectedFile) return;
  convertBtn.disabled = true;
  convertBtn.hidden = true;
  result.hidden = true;
  errorBox.hidden = true;
  setProgress(0.02, "Uploading…");

  const form = new FormData();
  form.append("file", selectedFile);
  form.append("cursor", $("opt-cursor").value);
  form.append("zooms", $("opt-zooms").value);
  form.append("webcam", $("opt-webcam").value);
  form.append("audio_cleanup", $("opt-audio").value);

  let jobId;
  try {
    const res = await fetch("/api/convert", { method: "POST", body: form });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || "Upload failed.");
    }
    jobId = (await res.json()).id;
  } catch (err) {
    showError(err.message || "Upload failed.");
    return;
  }

  setProgress(0.05, "Queued…");
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
