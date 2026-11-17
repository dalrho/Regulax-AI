document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('registerForm');
    const submitBtn = document.getElementById('submitRequestBtn');
    const passwordInput = document.getElementById('id_password');
    const confirmPasswordInput = document.getElementById('id_confirm_password');
    const complianceAck = document.getElementById('id_compliance_ack');

    if (!form) return;

    form.addEventListener('submit', function (e) {
        let isValid = true;

        // Clear existing client-side error highlights if re-submitting
        const fieldErrors = form.querySelectorAll('.client-error');
        fieldErrors.forEach(function (el) { el.remove(); });

        const inputs = form.querySelectorAll('.form-input');
        inputs.forEach(function (input) {
            input.classList.remove('input-has-error');
        });

        // Check password match
        if (passwordInput && confirmPasswordInput) {
            if (passwordInput.value && confirmPasswordInput.value && passwordInput.value !== confirmPasswordInput.value) {
                isValid = false;
                confirmPasswordInput.classList.add('input-has-error');
                const errDiv = document.createElement('div');
                errDiv.className = 'field-error-text client-error';
                errDiv.textContent = 'Passwords do not match.';
                confirmPasswordInput.parentNode.appendChild(errDiv);
            }
        }

        // Check compliance checkbox
        if (complianceAck && !complianceAck.checked) {
            isValid = false;
            complianceAck.classList.add('checkbox-has-error');
            const parent = complianceAck.closest('.compliance-group');
            if (parent && !parent.querySelector('.client-error')) {
                const errDiv = document.createElement('div');
                errDiv.className = 'field-error-text client-error';
                errDiv.style.marginLeft = '28px';
                errDiv.textContent = 'You must acknowledge that your actions are recorded and attributable.';
                parent.appendChild(errDiv);
            }
        }

        if (!isValid) {
            e.preventDefault();
            return;
        }

        // Prevent double submit
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = 'Submitting...';
        }
    });

    // Remove error class on input
    const allInputs = form.querySelectorAll('input');
    allInputs.forEach(function (input) {
        input.addEventListener('input', function () {
            this.classList.remove('input-has-error');
            this.classList.remove('checkbox-has-error');
            const parent = this.closest('.form-group') || this.closest('.compliance-group');
            if (parent) {
                const err = parent.querySelector('.client-error');
                if (err) err.remove();
            }
        });
    });
});

