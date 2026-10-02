// Progressive enhancement only; every feature works without JS.
document.addEventListener("submit", (e) => {
  const msg = e.target.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) e.preventDefault();
});
