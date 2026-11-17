document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('registerForm');
    const submitBtn = document.getElementById('submitRequestBtn');

    if (!form) return;

    /* ── Password visibility toggles ──────────────────────────────── */
    form.querySelectorAll('.reg-pw-toggle').forEach(function (btn) {
        btn.addEventListener('click', function () {
            const targetId = this.dataset.target;
            const input = document.getElementById(targetId);
            if (!input) return;

            const isHidden = input.type === 'password';
            input.type = isHidden ? 'text' : 'password';

            const show = this.querySelector('.eye-show');
            const hide = this.querySelector('.eye-hide');
            if (show) show.classList.toggle('is-hidden', isHidden);
            if (hide) hide.classList.toggle('is-hidden', !isHidden);
        });
    });

    /* ── Client-side validation on submit ─────────────────────────── */
    form.addEventListener('submit', function (e) {
        // Clear previous client-side errors
        form.querySelectorAll('.client-error').forEach(function (el) { el.remove(); });
        form.querySelectorAll('.has-error').forEach(function (el) { el.classList.remove('has-error'); });

        let valid = true;

        const pw   = document.getElementById('id_password');
        const cpw  = document.getElementById('id_confirm_password');
        const ack  = document.getElementById('id_compliance_ack');

        // Password match
        if (pw && cpw && pw.value && cpw.value && pw.value !== cpw.value) {
            valid = false;
            const wrap = cpw.closest('.reg-input-wrap');
            if (wrap) wrap.classList.add('has-error');
            insertError(cpw.closest('.reg-form-group'), 'Passwords do not match.', 'reg-field-error');
        }

        // Compliance checkbox
        if (ack && !ack.checked) {
            valid = false;
            const group = ack.closest('.reg-compliance-group');
            if (group && !group.querySelector('.client-error')) {
                insertError(group, 'You must acknowledge the compliance statement.', 'reg-field-error reg-field-error--indent');
            }
        }

        if (!valid) {
            e.preventDefault();
            return;
        }

        // Prevent double-submit
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = 'Submitting…';
        }
    });

    /* ── Clear error state on user input ──────────────────────────── */
    form.querySelectorAll('.reg-input').forEach(function (input) {
        input.addEventListener('input', function () {
            const wrap = this.closest('.reg-input-wrap');
            if (wrap) wrap.classList.remove('has-error');
            const group = this.closest('.reg-form-group');
            if (group) group.querySelectorAll('.client-error').forEach(function (el) { el.remove(); });
        });
    });

    const ack = document.getElementById('id_compliance_ack');
    if (ack) {
        ack.addEventListener('change', function () {
            const group = this.closest('.reg-compliance-group');
            if (group) group.querySelectorAll('.client-error').forEach(function (el) { el.remove(); });
        });
    }

    /* ── Helper ────────────────────────────────────────────────────── */
    function insertError(parent, message, cssClass) {
        if (!parent) return;
        const div = document.createElement('div');
        div.className = (cssClass || '') + ' client-error';
        div.textContent = message;
        parent.appendChild(div);
    }
});
