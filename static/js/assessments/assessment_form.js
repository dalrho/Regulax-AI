(() => {
    const primaryInputs = document.querySelectorAll('input[name="primary_version"]');
    const supportingInputs = document.querySelectorAll('input[name="supporting_versions"]');

    const syncDuplicates = () => {
        const primary = document.querySelector('input[name="primary_version"]:checked')?.value;
        supportingInputs.forEach((input) => {
            const isPrimary = input.value === primary;
            input.disabled = isPrimary;
            if (isPrimary) input.checked = false;
        });
    };

    primaryInputs.forEach((input) => input.addEventListener('change', syncDuplicates));
    syncDuplicates();
})();
