// Scoreboard hook for floppybird: after the game's own score screen, offer to save the score.
(function () {
  var form = document.getElementById('save'), nameEl = document.getElementById('name'), msg = document.getElementById('msg');
  try { nameEl.value = localStorage.getItem('flappy_name') || ''; } catch (e) {}

  var origShowScore = window.showScore;
  window.showScore = function () {
    origShowScore.apply(this, arguments);
    form.dataset.score = window.score;
    form.classList.add('show');
  };
  // Replay hides the form (the game's own replay button).
  document.getElementById('replay').addEventListener('click', function () { form.classList.remove('show'); });

  // Keys typed in the name field must not reach the game's document-level handlers.
  ['keydown', 'keyup', 'touchstart', 'mousedown'].forEach(function (t) {
    form.addEventListener(t, function (e) { e.stopPropagation(); });
  });

  function setMsg(text, ok) {
    msg.textContent = text; msg.className = ok ? 'ok' : 'bad';
    clearTimeout(setMsg.t); setMsg.t = setTimeout(function () { msg.className = ''; }, 6000);
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var name = nameEl.value.trim(), score = +form.dataset.score;
    try { localStorage.setItem('flappy_name', name); } catch (e) {}
    form.classList.remove('show');
    fetch('/api/scores', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name, score: score }) })
      .then(function (r) { return r.json(); })
      .then(function (j) { setMsg(j.ok ? 'Salvestatud! Koht #' + j.rank : 'Andmebaas ei vasta: ' + j.error, j.ok); })
      .catch(function () { setMsg('Server ei vasta', false); });
  });
})();
