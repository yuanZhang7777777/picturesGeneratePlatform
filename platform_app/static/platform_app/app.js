function csrfToken() {
  const token = document.querySelector('input[name="csrfmiddlewaretoken"]');
  return token ? token.value : "";
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken()
    },
    body: JSON.stringify(body || {})
  });
  return response.json();
}

async function uploadFiles(form) {
  const input = document.getElementById("file-input");
  const status = document.getElementById("upload-status");
  const data = new FormData();
  for (const file of input.files) data.append("files", file, file.webkitRelativePath || file.name);
  status.textContent = "上传中";
  const response = await fetch(form.dataset.uploadUrl, {
    method: "POST",
    headers: { "X-CSRFToken": csrfToken() },
    body: data
  });
  const body = await response.json();
  status.textContent = response.ok ? `已上传 ${body.asset_count} 个文件` : body.error;
  if (response.ok) window.location.reload();
}

async function refreshSnapshot() {
  const target = document.getElementById("snapshot-output");
  if (!target) return;
  const response = await fetch(target.dataset.url);
  if (!response.ok) return;
  const body = await response.json();
  target.textContent = JSON.stringify(body, null, 2);
  const terminal = ["completed", "partial", "failed"].includes(body.batch.status);
  if (!terminal) window.setTimeout(refreshSnapshot, document.hidden ? 15000 : 3000);
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("upload-form");
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      uploadFiles(form);
    });
  }

  const preflight = document.getElementById("preflight-button");
  if (preflight) {
    preflight.addEventListener("click", async () => {
      document.getElementById("preflight-output").textContent = JSON.stringify(
        await postJson(preflight.dataset.url),
        null,
        2
      );
    });
  }

  const confirm = document.getElementById("confirm-button");
  if (confirm) {
    confirm.addEventListener("click", async () => {
      document.getElementById("preflight-output").textContent = JSON.stringify(
        await postJson(confirm.dataset.url),
        null,
        2
      );
      refreshSnapshot();
    });
  }

  let draggedAssetId = null;
  for (const item of document.querySelectorAll("[data-asset-id]")) {
    item.addEventListener("dragstart", () => {
      draggedAssetId = item.dataset.assetId;
    });
  }

  for (const card of document.querySelectorAll(".cluster-card")) {
    card.addEventListener("dragover", (event) => event.preventDefault());
    card.addEventListener("drop", async (event) => {
      event.preventDefault();
      if (!draggedAssetId) return;
      const body = await postJson(card.dataset.mergeUrl, {
        asset_id: draggedAssetId,
        expected_version: Number(card.dataset.version)
      });
      if (body.error) alert(body.error);
      else window.location.reload();
    });
  }

  for (const button of document.querySelectorAll(".split-asset")) {
    button.addEventListener("click", async () => {
      const body = await postJson(button.dataset.url);
      if (body.error) alert(body.error);
      else window.location.reload();
    });
  }

  for (const button of document.querySelectorAll(".save-cluster")) {
    button.addEventListener("click", async () => {
      const card = button.closest(".cluster-card");
      const prompt = card.querySelector(".cluster-prompt").value;
      const body = await postJson(card.dataset.updateUrl, {
        expected_version: Number(card.dataset.version),
        prompt_override: prompt
      });
      if (body.error) alert(body.error);
      else window.location.reload();
    });
  }

  for (const button of document.querySelectorAll(".optimize-cluster")) {
    button.addEventListener("click", async () => {
      const card = button.closest(".cluster-card");
      button.disabled = true;
      const body = await postJson(card.dataset.optimizeUrl);
      button.disabled = false;
      if (body.error) alert(body.error);
      else card.querySelector(".cluster-prompt").value = body.suggested_prompt || "";
    });
  }

  refreshSnapshot();
});
