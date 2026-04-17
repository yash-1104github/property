import "./style.css";

const app = document.querySelector("#app");

app.innerHTML = `
  <main class="wrap">
    <h1>Property lookup</h1>
    <p class="hint">Calls the backend at <code>/api/v1/scrape</code> (Vite proxies to port 8000).</p>
    <form id="form" class="card">
      <label>Address
        <input name="address" type="text" required minlength="5"
          placeholder="21013 DANA Drive, Battle Creek, MI 49017"
          value="21013 DANA Drive, Battle Creek, MI 49017" />
      </label>
      <label>County (optional)
        <input name="county" type="text" placeholder="Calhoun" value="Calhoun" />
      </label>
      <label class="row">
        <input name="use_llm" type="checkbox" checked />
        Use LLM enrichment (needs GEMINI_API_KEY in repo root <code>.env</code>)
      </label>
      <button type="submit">Scrape</button>
    </form>
    <section id="status" class="status" hidden></section>
    <pre id="out" class="out" hidden></pre>
  </main>
`;

const form = document.querySelector("#form");
const out = document.querySelector("#out");
const statusEl = document.querySelector("#status");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(form);
  const body = {
    address: String(fd.get("address") || "").trim(),
    county: String(fd.get("county") || "").trim() || null,
    use_llm: fd.get("use_llm") === "on",
  };

  out.hidden = true;
  statusEl.hidden = false;
  statusEl.textContent = "Loading…";
  statusEl.className = "status loading";

  try {
    const res = await fetch("/api/v1/scrape", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    statusEl.textContent = res.ok ? `HTTP ${res.status}` : `HTTP ${res.status} — ${data.detail || data.error || "error"}`;
    statusEl.className = res.ok ? "status ok" : "status err";
    out.textContent = JSON.stringify(data, null, 2);
    out.hidden = false;
  } catch (err) {
    statusEl.textContent = String(err.message || err);
    statusEl.className = "status err";
  }
});
