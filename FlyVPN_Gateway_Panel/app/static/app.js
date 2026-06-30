document.addEventListener('click', async (e)=>{
  const el = e.target.closest('[data-copy]');
  if(!el) return;
  const text = el.getAttribute('data-copy');
  try{
    await navigator.clipboard.writeText(text);
    const old = el.textContent;
    el.textContent = 'Скопировано';
    setTimeout(()=>{el.textContent = old}, 1200);
  }catch(err){ prompt('Скопируй вручную:', text); }
});
