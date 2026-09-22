
document.addEventListener("DOMContentLoaded", () => {
  const search = document.getElementById("search");
  const cards = [...document.querySelectorAll(".archive-card")];
  const tagButtons = [...document.querySelectorAll(".tag-btn")];
  let activeTag = "all";

  function apply() {
    const q = (search?.value || "").toLowerCase().trim();
    cards.forEach((card) => {
      const tags = (card.getAttribute("data-tags") || "").toLowerCase();
      const text = card.innerText.toLowerCase();
      const tagOk = activeTag == "all" || tags.includes(activeTag);
      const qOk = !q || text.includes(q);
      card.style.display = tagOk && qOk ? "block" : "none";
    });
  }

  tagButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      tagButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      activeTag = (btn.getAttribute("data-tag") || "all").toLowerCase();
      apply();
    });
  });

  search?.addEventListener("input", apply);
});
