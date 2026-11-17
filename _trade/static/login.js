document.getElementById('f').onsubmit = async e => {
  e.preventDefault();
  const r = await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'meridian-trade'},
    body: JSON.stringify({password: document.getElementById('p').value})});
  if (r.ok) location.reload();
  else document.getElementById('e').textContent = (await r.json()).error || 'Wrong password';
};
