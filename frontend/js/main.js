const list = document.getElementById("status-list");

function row(label, value, good) {
  const icon = good ? "bi-check-circle-fill" : "bi-x-circle-fill";
  const li = document.createElement("li");
  li.className = "status-row";
  const name = document.createElement("span");
  name.textContent = label;
  const pill = document.createElement("span");
  pill.className = `pill ${good ? "ok" : "bad"}`;
  pill.innerHTML = `<i class="bi ${icon}" aria-hidden="true"></i>`;
  pill.append(document.createTextNode(value));
  li.append(name, pill);
  return li;
}

async function checkHealth() {
  try {
    const { ok, data } = await Api.get("/health");
    list.replaceChildren(
      row("API", ok ? "Running" : "Degraded", ok),
      row("Database", data?.database ?? "unknown", data?.database === "connected"),
      row("Environment", data?.environment ?? "unknown", true),
    );
    UI.toast(ok ? "Everything is running." : "Database is not reachable.", ok ? "success" : "error");
  } catch (_) {
    list.replaceChildren(row("API", "Unreachable", false));
    UI.toast("Can't reach the server. Is it running?", "error");
  }
}

document.getElementById("recheck").addEventListener("click", checkHealth);
checkHealth();
