// Fixed, reviewed observer injected into a recording session (ADR 0003: no arbitrary or
// generated JavaScript is ever executed). It never acts on the page - it only listens for
// clicks and completed field edits and reports what was touched through an exposed binding,
// so a person's own search actions can be replayed later as typed workflow steps. Password
// fields are never observed, not even their length.
(function () {
  function closestInteractive(el) {
    return el.closest('button, a, [role="button"], input, textarea, summary') || el;
  }

  function labelText(el) {
    if (el.labels && el.labels.length) return el.labels[0].innerText.trim();
    var labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      var parts = labelledBy
        .split(/\s+/)
        .map(function (id) {
          var node = document.getElementById(id);
          return node ? node.innerText.trim() : '';
        })
        .filter(Boolean);
      if (parts.length) return parts.join(' ');
    }
    var fieldset = el.closest('fieldset');
    if (fieldset) {
      var legend = fieldset.querySelector(':scope > legend');
      if (legend) return legend.innerText.trim();
    }
    return '';
  }

  function describe(el, kind, extra) {
    var descriptor = {
      kind: kind,
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      id: el.id || '',
      name: el.getAttribute('name') || '',
      role: el.getAttribute('role') || '',
      ariaLabel: (el.getAttribute('aria-label') || '').trim(),
      testId:
        el.getAttribute('data-testid') ||
        el.getAttribute('data-test-id') ||
        el.getAttribute('data-qa') ||
        '',
      placeholder: el.getAttribute('placeholder') || '',
      labelText: labelText(el),
      text: (el.innerText || el.value || '').trim().slice(0, 120)
    };
    for (var key in extra) descriptor[key] = extra[key];
    try {
      window.__jsaRecordEvent(JSON.stringify(descriptor));
    } catch (error) {
      /* recording is best-effort; a dropped event never blocks the page */
    }
  }

  document.addEventListener(
    'click',
    function (event) {
      var el = closestInteractive(event.target);
      if (!el || el === document.documentElement) return;
      describe(el, 'click', {});
    },
    true
  );

  document.addEventListener(
    'change',
    function (event) {
      var el = event.target;
      if (!el) return;
      var tag = el.tagName.toLowerCase();
      if (tag !== 'input' && tag !== 'textarea') return;
      var type = (el.getAttribute('type') || 'text').toLowerCase();
      var fillableTypes = ['text', 'search', 'email', 'tel', 'url', 'number'];
      if (tag === 'input' && fillableTypes.indexOf(type) === -1) return;
      var value = String(el.value || '');
      describe(el, 'fill', { value: value.slice(0, 200), valueLength: value.length });
    },
    true
  );
})();
