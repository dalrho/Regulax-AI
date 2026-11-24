/* Regulax AI — Dashboard JS */

(function () {
    'use strict';

    /* ------------------------------------------------------------------
       Greeting: Good morning / afternoon / evening
    ------------------------------------------------------------------ */
    function setGreeting() {
        const el = document.getElementById('dashGreeting');
        if (!el) return;
        const hour = new Date().getHours();
        let greeting;
        if (hour < 12)       greeting = 'Good morning.';
        else if (hour < 17)  greeting = 'Good afternoon.';
        else                 greeting = 'Good evening.';
        el.textContent = greeting;
    }

    /* ------------------------------------------------------------------
       Modal helpers
    ------------------------------------------------------------------ */
    const backdrop = document.getElementById('uploadModalBackdrop');

    function openModal() {
        backdrop.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        // Focus textarea for accessibility
        setTimeout(() => {
            const ta = document.getElementById('sopTextarea');
            if (ta) ta.focus();
        }, 120);
    }

    function closeModal() {
        backdrop.style.display = 'none';
        document.body.style.overflow = '';
    }

    /* ------------------------------------------------------------------
       Wire up "Upload SOP" buttons
    ------------------------------------------------------------------ */
    document.getElementById('uploadSopTopBtn')?.addEventListener('click', openModal);
    document.getElementById('uploadSopCardBtn')?.addEventListener('click', openModal);
    document.getElementById('uploadModalClose')?.addEventListener('click', closeModal);
    document.getElementById('uploadModalCancel')?.addEventListener('click', closeModal);

    // Close on backdrop click
    backdrop?.addEventListener('click', function (e) {
        if (e.target === backdrop) closeModal();
    });

    // Close on Escape
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && backdrop?.style.display === 'flex') {
            closeModal();
        }
    });

    /* ------------------------------------------------------------------
       Drag & drop for file input
    ------------------------------------------------------------------ */
    const dropArea   = document.getElementById('uploadDropArea');
    const fileInput  = document.getElementById('sopFileInput');
    const textarea   = document.getElementById('sopTextarea');

    if (dropArea && fileInput) {
        ['dragenter', 'dragover'].forEach(evt => {
            dropArea.addEventListener(evt, (e) => {
                e.preventDefault();
                dropArea.classList.add('is-drag-over');
            });
        });

        ['dragleave', 'drop'].forEach(evt => {
            dropArea.addEventListener(evt, (e) => {
                e.preventDefault();
                dropArea.classList.remove('is-drag-over');
            });
        });

        dropArea.addEventListener('drop', (e) => {
            const file = e.dataTransfer?.files?.[0];
            if (file && file.name.endsWith('.txt')) {
                readFileIntoTextarea(file);
            }
        });

        fileInput.addEventListener('change', () => {
            const file = fileInput.files?.[0];
            if (file) readFileIntoTextarea(file);
        });
    }

    function readFileIntoTextarea(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            if (textarea) textarea.value = e.target.result;
        };
        reader.readAsText(file);
    }

    /* ------------------------------------------------------------------
       Submit placeholder
    ------------------------------------------------------------------ */
    document.getElementById('uploadModalSubmit')?.addEventListener('click', function () {
        const text = textarea?.value?.trim();
        if (!text) {
            textarea?.focus();
            textarea?.classList.add('upload-textarea--error');
            setTimeout(() => textarea?.classList.remove('upload-textarea--error'), 1200);
            return;
        }
        // TODO: wire to real backend upload
        alert('SOP uploaded! (placeholder — wire to your backend endpoint)');
        closeModal();
        if (textarea) textarea.value = '';
        if (fileInput) fileInput.value = '';
    });

    /* ------------------------------------------------------------------
       Init
    ------------------------------------------------------------------ */
    setGreeting();
})();

