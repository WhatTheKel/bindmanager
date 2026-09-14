/* BindManager – app.js */
(function () {
  'use strict';

  function $  (sel, ctx) { return (ctx || document).querySelector(sel); }
  function $$ (sel, ctx) { return Array.from((ctx || document).querySelectorAll(sel)); }

  /* ── Progress bar on navigation ───────────────────────────── */
  var bar = $('#progress-bar');
  if (bar && window.gsap) {
    $$('a[href]:not([href^="#"]):not([target="_blank"]):not([data-delete-url])').forEach(function (a) {
      a.addEventListener('click', function () {
        gsap.killTweensOf(bar);
        gsap.set(bar, { opacity: 1 });
        gsap.to(bar, { width: '72%', duration: .5, ease: 'power1.out' });
      });
    });
    window.addEventListener('load', function () {
      gsap.killTweensOf(bar);
      var tl = gsap.timeline();
      tl.to(bar, { width: '100%', duration: .2, ease: 'power1.out' })
        .to(bar, { opacity: 0, duration: .3 }, '+=0.15')
        .set(bar, { width: 0, opacity: 1 });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {

    /* ── Delete confirm modal ──────────────────────────────── */
    var modal       = $('#delete-modal');
    var modalBox    = modal ? $('.modal-box', modal) : null;
    var modalMsg    = $('#delete-modal-msg');
    var modalForm   = $('#delete-modal-form');
    var modalCancel = $('#delete-modal-cancel');
    var modalOpen   = false;

    function openDelete(url, label) {
      modalMsg.textContent = label;
      modalForm.action = url;
      modal.style.display = 'flex';
      modalOpen = true;
      if (window.gsap) {
        gsap.fromTo(modal,    { opacity: 0 }, { opacity: 1, duration: .15 });
        gsap.fromTo(modalBox, { opacity: 0, y: 12, scale: .96 },
                               { opacity: 1, y: 0, scale: 1, duration: .22, ease: 'back.out(1.7)' });
      }
      modalCancel.focus();
    }

    function closeDelete() {
      if (!modalOpen) return;
      modalOpen = false;
      if (window.gsap) {
        gsap.to(modalBox, { opacity: 0, y: 8, scale: .96, duration: .15, ease: 'power1.in' });
        gsap.to(modal, {
          opacity: 0, duration: .15, delay: .03,
          onComplete: function () { modal.style.display = 'none'; gsap.set(modal, { clearProps: 'opacity' }); }
        });
      } else {
        modal.style.display = 'none';
      }
    }

    function shakeModal() {
      if (!window.gsap || !modalBox) return;
      gsap.fromTo(modalBox, { x: 0 },
        { x: 8, duration: .06, repeat: 5, yoyo: true, ease: 'power1.inOut', clearProps: 'x' });
    }

    if (modal) {
      modalCancel.addEventListener('click', closeDelete);
      modal.addEventListener('click', function (e) { if (e.target === modal) closeDelete(); });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && modalOpen) closeDelete();
      });
      /* Guard against double-submit: shake instead of firing twice */
      modalForm.addEventListener('submit', function (e) {
        if (modalForm.dataset.submitting) { e.preventDefault(); shakeModal(); return; }
        modalForm.dataset.submitting = '1';
      });
    }

    $$('[data-delete-url]').forEach(function (el) {
      el.addEventListener('click', function (e) {
        e.preventDefault();
        openDelete(el.dataset.deleteUrl, el.dataset.deleteLabel || 'Delete this item? This cannot be undone.');
      });
    });

    /* ── Toast slide-in + auto-dismiss ─────────────────────── */
    $$('.toast').forEach(function (t) {
      if (window.gsap) {
        gsap.set(t, { maxHeight: t.offsetHeight });
        gsap.from(t, { x: 24, opacity: 0, duration: .3, ease: 'power2.out' });
      }
      setTimeout(function () {
        if (!window.gsap) {
          if (t.parentNode) t.parentNode.removeChild(t);
          return;
        }
        gsap.to(t, {
          x: 24, opacity: 0, maxHeight: 0, paddingTop: 0, paddingBottom: 0, marginTop: 0, marginBottom: 0,
          duration: .35, ease: 'power1.in',
          onComplete: function () { if (t.parentNode) t.parentNode.removeChild(t); }
        });
      }, 4500);
    });

    /* ── Live search – debounced auto-submit ───────────────── */
    $$('[data-live-search]').forEach(function (input) {
      var timer;
      input.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(function () { input.closest('form').submit(); }, 400);
      });
    });

    /* ── Form submit: disable button to prevent double-click ── */
    $$('form:not(#delete-modal-form)').forEach(function (form) {
      form.addEventListener('submit', function () {
        var btn = form.querySelector('button[type="submit"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        btn.dataset.origText = btn.textContent;
        btn.textContent = 'Saving…';
        setTimeout(function () {
          btn.disabled = false;
          btn.textContent = btn.dataset.origText;
        }, 10000);
      });
    });

    /* ── User menu dropdown ────────────────────────────────── */
    var trigger  = $('#user-menu-trigger');
    var dropdown = $('#user-menu-dropdown');
    if (trigger && dropdown) {
      trigger.addEventListener('click', function (e) {
        e.stopPropagation();
        var open = !dropdown.hidden;
        dropdown.hidden = open;
        trigger.setAttribute('aria-expanded', String(!open));
      });
      document.addEventListener('click', function () {
        dropdown.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { dropdown.hidden = true; trigger.setAttribute('aria-expanded', 'false'); }
      });
    }

    /* ── Stat card count-up ────────────────────────────────── */
    if (window.gsap) {
      $$('.stat-card .value').forEach(function (el) {
        var target = parseInt(el.textContent.replace(/[^\d-]/g, ''), 10);
        if (isNaN(target)) return;
        var counter = { n: 0 };
        gsap.to(counter, {
          n: target, duration: .8, ease: 'power2.out', delay: .1,
          onUpdate: function () { el.textContent = Math.round(counter.n); },
          onComplete: function () { el.textContent = target; }
        });
      });
    }

    /* ── Table row stagger-in ──────────────────────────────── */
    if (window.gsap) {
      $$('.table-wrap tbody').forEach(function (tbody) {
        var rows = $$('tr', tbody);
        if (!rows.length) return;
        gsap.from(rows, { opacity: 0, y: 8, duration: .35, stagger: .035, ease: 'power2.out' });
      });
    }

    /* ── Pending-sync pulse ─────────────────────────────────── */
    if (window.gsap) {
      var pendingDots = $$('.dot-red');
      if (pendingDots.length) {
        gsap.to(pendingDots, {
          scale: 1.5, opacity: .35, duration: .75, ease: 'sine.inOut',
          repeat: -1, yoyo: true, stagger: .15, transformOrigin: '50% 50%'
        });
      }
    }

  });
})();
