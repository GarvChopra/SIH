/* SAMANVAY shell: nav drawer, modals, toasts, institution picker. Progressive
   enhancement: every page works as plain links and forms without this file. */
(() => {
  "use strict";
  const body = document.body;

  // mobile drawer
  const setDrawer = (open) => body.classList.toggle("drawer", open);
  const burger = document.getElementById("burger");
  if (burger) burger.addEventListener("click", () => setDrawer(!body.classList.contains("drawer")));
  document.querySelectorAll("[data-close-drawer]").forEach((el) =>
    el.addEventListener("click", () => setDrawer(false)));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setDrawer(false); });

  // <dialog> modals: <button data-open="dlg-id"> ... <button data-close>
  document.querySelectorAll("[data-open]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const d = document.getElementById(btn.dataset.open);
      if (d && d.showModal) d.showModal();
    }));
  document.querySelectorAll("dialog [data-close]").forEach((btn) =>
    btn.addEventListener("click", () => btn.closest("dialog").close()));
  document.querySelectorAll("dialog").forEach((d) =>
    d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

  // flash messages -> auto-dismissing toasts
  document.querySelectorAll(".toast").forEach((t) =>
    setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; }, 5200));

  // disable submit buttons after click (no double submits)
  document.querySelectorAll("form").forEach((f) =>
    f.addEventListener("submit", (e) => {
      setTimeout(() => {
        if (e.defaultPrevented) return;
        f.querySelectorAll("button[type=submit]").forEach((b) => { b.disabled = true; });
      }, 0);
    }));

  // copy tracking id
  document.querySelectorAll("[data-copy]").forEach((b) =>
    b.addEventListener("click", () => {
      navigator.clipboard && navigator.clipboard.writeText(b.dataset.copy).then(() => {
        const o = b.textContent; b.textContent = "Copied"; setTimeout(() => { b.textContent = o; }, 1400);
      });
    }));

  // demo-account quick fill on the login page
  document.querySelectorAll("[data-fill-user]").forEach((b) =>
    b.addEventListener("click", () => {
      document.getElementById("username").value = b.dataset.fillUser;
      document.getElementById("pin").value = b.dataset.fillPin;
      document.getElementById("pin").focus();
    }));

  // case board: drag a card to a column. The drop only opens the right form; the
  // server validates the move, applies it, and writes the history/audit entry.
  const board = document.querySelector(".kanban");
  if (board) {
    const dlg = document.getElementById("dlg-move");
    const form = document.getElementById("move-form");
    const $ = (id) => document.getElementById(id);
    let dragged = null;
    const show = (id, on) => { $(id).hidden = !on; };
    const toast = (msg) => {
      let box = document.querySelector(".toasts");
      if (!box) { box = document.createElement("div"); box.className = "toasts"; document.body.appendChild(box); }
      const t = document.createElement("div"); t.className = "toast err"; t.textContent = msg; box.appendChild(t);
      setTimeout(() => t.remove(), 4500);
    };
    board.querySelectorAll(".kcard").forEach((card) => {
      card.addEventListener("dragstart", (e) => {
        if (!card.dataset.moves) { e.preventDefault(); return; }
        dragged = card; card.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        try { e.dataTransfer.setData("text/plain", card.dataset.case); } catch (err) {}
        const moves = card.dataset.moves.split(",");
        board.querySelectorAll(".kcol").forEach((col) => {
          col.classList.toggle("drop-ok", moves.includes(col.dataset.status));
          col.classList.toggle("drop-no", col.dataset.status !== card.dataset.from && !moves.includes(col.dataset.status));
        });
      });
      card.addEventListener("dragend", () => {
        card.classList.remove("dragging"); dragged = null;
        board.querySelectorAll(".kcol").forEach((c) => c.classList.remove("drop-ok", "drop-no"));
      });
    });
    board.querySelectorAll(".kcol").forEach((col) => {
      col.addEventListener("dragover", (e) => {
        if (dragged && dragged.dataset.moves.split(",").includes(col.dataset.status)) e.preventDefault();
      });
      col.addEventListener("drop", (e) => {
        e.preventDefault();
        if (!dragged) return;
        const to = col.dataset.status, from = dragged.dataset.from, num = dragged.dataset.case;
        const moves = dragged.dataset.moves.split(",");
        if (to === from) return;
        if (!moves.includes(to)) { toast("That move is not allowed for " + num + " (" + from + " to " + to + ")."); return; }
        $("move-next").value = location.pathname + location.search;
        $("move-sub").textContent = num + ": " + from + " → " + to;
        $("move-title").textContent = "Move to " + to;
        $("move-remarks").value = ""; $("move-citizen").value = ""; $("move-bureau").value = "";
        $("move-remarks").required = false; $("move-bureau").required = false; $("move-citizen").required = false;
        show("move-bureau-wrap", false); show("move-remarks-wrap", true); show("move-citizen-wrap", false);
        $("move-to").disabled = false; $("move-to").value = to;
        const label = $("move-remarks-label");
        if (to === "Pending") {                                   // accept a transfer
          form.action = "/cases/" + num + "/accept"; show("move-remarks-wrap", false); $("move-to").disabled = true;
          $("move-go").textContent = "Accept transfer";
        } else if (to === "Transferred") {
          form.action = "/cases/" + num + "/transfer"; show("move-bureau-wrap", true); $("move-bureau").required = true;
          label.textContent = "Reason"; $("move-go").textContent = "Transfer"; $("move-to").disabled = true;
        } else if (to === "Escalated") {
          form.action = "/cases/" + num + "/escalate"; label.textContent = "Why is this being escalated?";
          $("move-remarks").required = true; $("move-go").textContent = "Escalate"; $("move-to").disabled = true;
        } else {                                                  // Under Process / Disposed
          form.action = "/cases/" + num + "/status";
          if (to === "Disposed") {
            label.textContent = "Disposal remark (internal)"; $("move-remarks").required = true;
            if (dragged.dataset.citizen === "1") { show("move-citizen-wrap", true); $("move-citizen").required = true; }
          } else {
            label.textContent = from === "Disposed" ? "Reason for reopening" : "Remarks (optional)";
            $("move-remarks").required = from === "Disposed";
          }
          $("move-go").textContent = "Move";
        }
        dlg.showModal();
      });
    });
  }

  // institution picker (citizen complaint form): searches the public registry
  const box = document.getElementById("inst-search");
  if (box) {
    const list = document.getElementById("inst-results");
    const hidden = document.getElementById("aicte_id");
    const picked = document.getElementById("inst-picked");
    const pickedName = document.getElementById("inst-picked-name");
    const clear = document.getElementById("inst-clear");
    let timer, ctl;
    const pick = (it) => {
      hidden.value = it.aicte_id;
      pickedName.textContent = it.name + " (AICTE ID " + it.aicte_id + ")";
      picked.hidden = false; box.hidden = true; list.hidden = true; box.value = "";
    };
    const render = (items) => {
      list.innerHTML = "";
      if (!items.length) {
        const li = document.createElement("li");
        li.innerHTML = "<span>No institution found. Try the name or the AICTE ID.</span>";
        list.appendChild(li); list.hidden = false; return;
      }
      items.forEach((it) => {
        const li = document.createElement("li");
        const b = document.createElement("b"); b.textContent = it.name;
        const s = document.createElement("span");
        s.textContent = "AICTE ID " + it.aicte_id + (it.region ? " · " + it.region : "");
        li.append(b, s); li.addEventListener("click", () => pick(it)); list.appendChild(li);
      });
      list.hidden = false;
    };
    box.addEventListener("input", () => {
      clearTimeout(timer);
      const q = box.value.trim();
      if (q.length < 2) { list.hidden = true; return; }
      timer = setTimeout(() => {
        if (ctl) ctl.abort(); ctl = new AbortController();
        fetch("/my/institutions/search?q=" + encodeURIComponent(q), { signal: ctl.signal })
          .then((r) => r.json()).then(render).catch(() => {});
      }, 200);
    });
    clear.addEventListener("click", () => {
      hidden.value = ""; picked.hidden = true; box.hidden = false; box.focus();
    });
    document.addEventListener("click", (e) => { if (!list.contains(e.target) && e.target !== box) list.hidden = true; });
    const form = box.closest("form");
    form.addEventListener("submit", (e) => {
      if (!hidden.value) { e.preventDefault(); box.focus(); box.setCustomValidity("Choose an institution from the list"); box.reportValidity(); box.setCustomValidity(""); }
    });
  }
})();
