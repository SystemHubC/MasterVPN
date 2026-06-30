(function(){
  document.querySelectorAll('[data-copy]').forEach(btn=>{
    btn.addEventListener('click', async ()=>{
      try{ await navigator.clipboard.writeText(btn.getAttribute('data-copy')||''); btn.textContent='Copied ✓'; setTimeout(()=>btn.textContent='Copy secure sub',1200); }
      catch(e){ alert('Не смог скопировать: '+e); }
    });
  });
})();
