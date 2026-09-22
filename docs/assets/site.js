// LLM Evaluation Radar Client Scripts
document.addEventListener('DOMContentLoaded', () => {
  // Reading Progress Bar
  const progressBar = document.getElementById('progress-bar');
  if (progressBar) {
    window.addEventListener('scroll', () => {
      const total = document.documentElement.scrollHeight - window.innerHeight;
      const progress = (window.scrollY / total) * 100;
      progressBar.style.width = Math.min(100, Math.max(0, progress)) + '%';
    });
  }

  // Tag Filtering on Index
  const tagButtons = document.querySelectorAll('.tag-filter-btn');
  const reportCards = document.querySelectorAll('.report-card');
  const searchInput = document.getElementById('report-search');

  function applyFilters() {
    const activeBtn = document.querySelector('.tag-filter-btn.active');
    const selectedTag = activeBtn ? activeBtn.getAttribute('data-tag').toLowerCase() : 'all';
    const query = searchInput ? searchInput.value.toLowerCase().trim() : '';

    reportCards.forEach(card => {
      const cardTags = (card.getAttribute('data-tags') || '').toLowerCase();
      const cardText = card.innerText.toLowerCase();

      const matchesTag = (selectedTag === 'all') || cardTags.includes(selectedTag);
      const matchesQuery = !query || cardText.includes(query);

      if (matchesTag && matchesQuery) {
        card.style.display = 'flex';
      } else {
        card.style.display = 'none';
      }
    });
  }

  tagButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      tagButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyFilters();
    });
  });

  if (searchInput) {
    searchInput.addEventListener('input', applyFilters);
  }
});
