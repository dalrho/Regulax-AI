(() => {
    const main = document.querySelector('[data-progress-url]');
    if (!main) return;
    let terminalReloaded = false;

    const refresh = async () => {
        try {
            const response = await fetch(main.dataset.progressUrl, {
                headers: { Accept: 'application/json' },
                credentials: 'same-origin',
            });
            if (!response.ok) return;
            const data = await response.json();
            const status = document.getElementById('assessmentStatus');
            const accounted = document.getElementById('progressAccounted');
            const findingCount = document.getElementById('progressFindingCount');
            const excludedCount = document.getElementById('progressExcludedCount');
            const progress = document.getElementById('assessmentProgressBar');
            if (status) {
                status.textContent = data.status.charAt(0).toUpperCase() + data.status.slice(1);
                status.className = `status-pill status-${data.status}`;
            }
            if (accounted) accounted.textContent = `${data.accounted} of ${data.total}`;
            if (findingCount) findingCount.textContent = data.findings;
            if (excludedCount) excludedCount.textContent = data.excluded;
            if (progress) progress.style.width = `${data.total ? Math.round(data.accounted * 100 / data.total) : 0}%`;
            if (!terminalReloaded && ['completed', 'failed'].includes(data.status)) {
                terminalReloaded = true;
                window.location.reload();
            }
        } catch (_) {
            // A later poll will recover from transient network or session failures.
        }
    };

    window.setInterval(refresh, 5000);
    refresh();
})();
