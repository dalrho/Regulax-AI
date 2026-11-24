(function () {
    'use strict';

    const dialog = document.getElementById('uploadDialog');
    const openButton = document.getElementById('openUpload');
    const closeButton = document.getElementById('closeUpload');
    const cancelButton = document.getElementById('cancelUpload');
    const fileInput = document.getElementById('sopFile');
    const titleInput = document.getElementById('sopTitle');

    openButton?.addEventListener('click', () => dialog?.showModal());
    closeButton?.addEventListener('click', () => dialog?.close());
    cancelButton?.addEventListener('click', () => dialog?.close());
    dialog?.addEventListener('click', (event) => {
        if (event.target === dialog) dialog.close();
    });
    fileInput?.addEventListener('change', () => {
        const file = fileInput.files?.[0];
        if (file && !titleInput.value.trim()) {
            titleInput.value = file.name.replace(/\.txt$/i, '').replace(/[_-]+/g, ' ');
        }
    });

    if (document.querySelector('.sop-error')) dialog?.showModal();
})();
