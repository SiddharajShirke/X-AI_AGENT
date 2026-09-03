document.querySelectorAll('[data-account-switch]').forEach((select) => {
  select.addEventListener('change', () => {
    window.location.assign(`/accounts/${select.value}`);
  });
});

document.querySelectorAll('[data-toggle="add-account"]').forEach((button) => {
  button.addEventListener('click', () => {
    const panel = document.querySelector('[data-add-account]');
    if (panel) {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) panel.querySelector('input[name="name"]')?.focus();
    }
  });
});

document.querySelectorAll('[data-reject-form]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (!window.confirm('Reject this draft, save the feedback, and generate a materially different version?')) {
      event.preventDefault();
    }
  });
});

document.querySelectorAll('[data-approve-form]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (form.dataset.live !== 'true') return;
    const text = form.closest('[id^="draft-"]')?.querySelector('[data-post-text]')?.textContent?.trim() || '';
    const prompt = `Publish this exact post to @${form.dataset.handle}?\n\n${text}`;
    if (!window.confirm(prompt)) event.preventDefault();
  });
});

document.querySelectorAll('[data-edit-form]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    const approve = form.querySelector('input[name="approve"]');
    if (!approve?.checked || form.dataset.live !== 'true') return;
    const text = form.querySelector('textarea[name="text"]')?.value?.trim() || '';
    const prompt = `Publish this edited post to @${form.dataset.handle}?\n\n${text}`;
    if (!window.confirm(prompt)) event.preventDefault();
  });
});

document.querySelectorAll('[data-character-count]').forEach((field) => {
  const output = field.parentElement?.querySelector('[data-count-output]');
  const render = () => { if (output) output.textContent = `${field.value.length}/280`; };
  field.addEventListener('input', render);
  render();
});
