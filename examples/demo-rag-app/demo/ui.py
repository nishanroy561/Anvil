"""The demo app's one page: ask a question, then rate the answer.

This is deliberately **not** styled like the Anvil dashboard. It stands in for a
customer's own product — the thing being observed — and the demo tells a clearer
story if the two are visibly different surfaces: a support widget here, an
observability console there. Anvil's own palette and type would blur exactly the
boundary the SDK crosses.

Plain HTML in a string, no template engine and no build step, because the point
of the file is the twelve lines where the thumbs-down posts a `trace_id`.
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Northwind Support — demo app</title>
<style>
  :root {
    --paper: #fbfaf8;
    --card: #ffffff;
    --edge: #e4e1db;
    --text: #1c1b19;
    --soft: #6b6862;
    --accent: #7a5c2e;      /* a warm graphite-gold: nothing like Anvil's blue */
    --down: #b54708;
    --up: #047857;
    --radius: 10px;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 40px 20px 64px;
    background: var(--paper);
    color: var(--text);
    font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  main { max-width: 620px; margin: 0 auto; }
  .eyebrow {
    font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
    color: var(--soft); margin: 0 0 6px;
  }
  h1 { font-size: 25px; line-height: 1.2; margin: 0 0 6px; letter-spacing: -.01em; }
  .lede { margin: 0 0 26px; color: var(--soft); font-size: 14px; }
  .card {
    background: var(--card); border: 1px solid var(--edge);
    border-radius: var(--radius); padding: 18px;
  }
  label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
  input[type=text], textarea {
    width: 100%; font: inherit; font-size: 15px; color: var(--text);
    background: var(--paper); border: 1px solid var(--edge);
    border-radius: 8px; padding: 11px 12px;
  }
  input[type=text]:focus, textarea:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
  textarea { resize: vertical; min-height: 68px; }
  button {
    font: inherit; font-size: 14px; cursor: pointer; border-radius: 8px;
    border: 1px solid var(--edge); background: var(--card); color: var(--text);
    /* End users, not operators: every target clears 44px. */
    min-height: 44px; padding: 0 16px;
    transition: background .15s ease, border-color .15s ease, color .15s ease;
  }
  button:hover { border-color: #cfcbc3; }
  button.primary { background: var(--text); border-color: var(--text); color: #fff; }
  button.primary:hover { background: #34322e; }
  button[disabled] { opacity: .5; cursor: not-allowed; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 14px; }
  .answer { margin: 22px 0 0; }
  .answer p.text { font-size: 16px; margin: 0 0 10px; }
  .sources { margin: 0; padding: 0; list-style: none; font-size: 13px; color: var(--soft); }
  .sources li { padding: 4px 0 4px 12px; border-left: 2px solid var(--edge); margin-bottom: 4px; }
  .vote { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--edge); }
  .vote h2 { font-size: 13px; font-weight: 600; margin: 0 0 10px; }
  button.down[aria-pressed=true] { border-color: var(--down); color: var(--down); background: #fdf4ec; }
  button.up[aria-pressed=true] { border-color: var(--up); color: var(--up); background: #eefaf4; }
  .note { font-size: 13px; color: var(--soft); margin: 12px 0 0; }
  .trace { font-family: ui-monospace, monospace; font-size: 12px; color: var(--soft); }
  .sent { border-left: 2px solid var(--down); padding-left: 12px; }
  .sent.up { border-left-color: var(--up); }
  .hidden { display: none; }
  footer { margin-top: 28px; font-size: 12px; color: var(--soft); }
  code { font-family: ui-monospace, monospace; font-size: 12px; }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
</head>
<body>
<main>
  <p class="eyebrow">Northwind Support</p>
  <h1>Ask about orders, refunds, or your account</h1>
  <p class="lede">
    A toy RAG app instrumented with the Anvil SDK. Ask something, then rate the answer —
    a thumbs-down travels back to Anvil and lands in the failure inbox.
  </p>

  <form class="card" id="ask-form">
    <label for="q">Your question</label>
    <input id="q" name="q" type="text" required
           value="How long do refunds take?"
           placeholder="How long do refunds take?" />
    <div class="row">
      <button class="primary" type="submit" id="ask">Ask</button>
      <span class="note" id="status" role="status" aria-live="polite"></span>
    </div>
  </form>

  <section class="answer hidden" id="answer" aria-live="polite">
    <div class="card">
      <p class="text" id="answer-text"></p>
      <ul class="sources" id="sources"></ul>

      <div class="vote">
        <h2 id="vote-label">Was this answer right?</h2>
        <div class="row" role="group" aria-labelledby="vote-label">
          <button type="button" class="up" id="vote-up" aria-pressed="false">
            &#128077; Yes, that helped
          </button>
          <button type="button" class="down" id="vote-down" aria-pressed="false">
            &#128078; No, that's wrong
          </button>
        </div>

        <div class="hidden" id="comment-block">
          <div style="margin-top:14px">
            <label for="comment">What was wrong? (optional)</label>
            <textarea id="comment" placeholder="It said 5 days. My refund took three weeks."></textarea>
          </div>
          <div class="row">
            <button class="primary" type="button" id="send-down">Send feedback</button>
            <button type="button" id="cancel-down">Cancel</button>
          </div>
        </div>

        <p class="note hidden" id="sent"></p>
        <p class="trace" id="trace"></p>
      </div>
    </div>
  </section>

  <footer>
    One SDK call does this: <code>anvil.feedback(trace_id, -1, comment=…, user_ref=…)</code>.
    The vote is safe to send immediately — Anvil holds it until the trace itself lands.
  </footer>
</main>

<script>
  const el = (id) => document.getElementById(id);
  // The id /ask hands back. Round-tripping it is the entire integration.
  let traceId = null;
  const USER = "demo-visitor";

  el("ask-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    el("ask").disabled = true;
    el("status").textContent = "Thinking…";
    el("answer").classList.add("hidden");
    resetVote();
    try {
      const res = await fetch("/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: el("q").value, user_ref: USER }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const body = await res.json();
      traceId = body.trace_id;
      el("answer-text").textContent = body.answer;
      el("sources").innerHTML = "";
      for (const s of body.sources || []) {
        const li = document.createElement("li");
        li.textContent = s;
        el("sources").appendChild(li);
      }
      el("trace").textContent = traceId ? "trace " + traceId : "";
      el("answer").classList.remove("hidden");
      el("status").textContent = "";
    } catch (err) {
      // Say what happened and how to fix it, in the app's voice.
      el("status").textContent = "Couldn't reach the assistant. Is the demo app still running?";
    } finally {
      el("ask").disabled = false;
    }
  });

  function resetVote() {
    el("vote-up").setAttribute("aria-pressed", "false");
    el("vote-down").setAttribute("aria-pressed", "false");
    el("comment-block").classList.add("hidden");
    el("sent").classList.add("hidden");
    el("comment").value = "";
  }

  el("vote-down").addEventListener("click", () => {
    el("vote-down").setAttribute("aria-pressed", "true");
    el("vote-up").setAttribute("aria-pressed", "false");
    el("comment-block").classList.remove("hidden");
    el("comment").focus();
  });

  el("cancel-down").addEventListener("click", () => {
    el("comment-block").classList.add("hidden");
    el("vote-down").setAttribute("aria-pressed", "false");
  });

  el("vote-up").addEventListener("click", () => {
    el("vote-up").setAttribute("aria-pressed", "true");
    el("vote-down").setAttribute("aria-pressed", "false");
    el("comment-block").classList.add("hidden");
    send(1, null);
  });

  el("send-down").addEventListener("click", () => {
    el("comment-block").classList.add("hidden");
    send(-1, el("comment").value.trim() || null);
  });

  async function send(rating, comment) {
    if (!traceId) return;
    const sent = el("sent");
    sent.classList.remove("hidden");
    sent.className = "note sent" + (rating > 0 ? " up" : "");
    sent.textContent = "Sending…";
    try {
      const res = await fetch("/feedback", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ trace_id: traceId, rating, comment, user_ref: USER }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      sent.textContent =
        rating > 0
          ? "Thanks — recorded as a thumbs-up in Anvil."
          : "Thanks. Recorded as a thumbs-down — it's now in Anvil's failure inbox, ready to promote into a dataset.";
    } catch (err) {
      sent.textContent = "Couldn't send that. The answer is unaffected; try again.";
    }
  }
</script>
</body>
</html>
"""
