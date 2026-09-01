document.querySelectorAll('form').forEach((form) => {
  if (form.action.includes('/reject')) {
    form.addEventListener('submit', (event) => {
      if (!window.confirm('Reject this draft, store the lesson, and generate a materially different post?')) {
        event.preventDefault();
      }
    });
  }
});
