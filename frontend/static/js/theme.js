/* Theme toggle — runs after DOM is ready */
(function () {
  function getStored() { try { return localStorage.getItem('theme'); } catch (_) { return null; } }
  function setStored(t)  { try { localStorage.setItem('theme', t); } catch (_) {} }

  function label(theme) {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    var text = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
    btn.setAttribute('aria-label', text);
    btn.setAttribute('title', text);
  }

  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    setStored(theme);
    label(theme);
  }

  /* Apply on load (also handled inline in <head> to prevent flash) */
  var stored = getStored();
  var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  apply(stored || (prefersDark ? 'dark' : 'light'));

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    label(document.documentElement.getAttribute('data-theme'));

    btn.addEventListener('click', function () {
      var current = document.documentElement.getAttribute('data-theme');
      var next    = current === 'dark' ? 'light' : 'dark';

      var sun  = btn.querySelector('.icon-sun');
      var moon = btn.querySelector('.icon-moon');

      var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (!window.gsap || !sun || !moon || reduceMotion) { apply(next); return; }

      // The icon shown is the target theme: sun while dark, moon while light
      var showing = current === 'dark' ? sun : moon;
      var hidden  = showing === moon ? sun : moon;

      gsap.killTweensOf([sun, moon]);
      gsap.set(hidden, { display: 'inline-block', opacity: 0, rotation: -90, scale: .4 });

      gsap.timeline({
        onComplete: function () { gsap.set([sun, moon], { clearProps: 'display,opacity,rotation,scale' }); }
      })
        .to(showing, { opacity: 0, rotation: 90, scale: .4, duration: .2, ease: 'power1.in' }, 0)
        .to(hidden,  { opacity: 1, rotation: 0,  scale: 1,  duration: .3, ease: 'back.out(2)' }, .05);

      apply(next);
    });
  });
})();
