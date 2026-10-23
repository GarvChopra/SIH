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
