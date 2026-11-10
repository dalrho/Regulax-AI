/**
 * Regulax AI - Login Screen Client Logic
 */

document.addEventListener('DOMContentLoaded', function () {
    // 1. Password Visibility Toggle
    const passwordInput = document.getElementById('id_password');
    const togglePasswordBtn = document.getElementById('togglePasswordBtn');
    const eyeIconShow = document.getElementById('eyeIconShow');
    const eyeIconHide = document.getElementById('eyeIconHide');

    if (togglePasswordBtn && passwordInput) {
        togglePasswordBtn.addEventListener('click', function (e) {
            e.preventDefault();
            const isPassword = passwordInput.getAttribute('type') === 'password';

            if (isPassword) {
                passwordInput.setAttribute('type', 'text');
                eyeIconShow.classList.add('is-hidden');
                eyeIconHide.classList.remove('is-hidden');
                togglePasswordBtn.setAttribute('aria-label', 'Hide password');
            } else {
                passwordInput.setAttribute('type', 'password');
                eyeIconShow.classList.remove('is-hidden');
                eyeIconHide.classList.add('is-hidden');
                togglePasswordBtn.setAttribute('aria-label', 'Show password');
            }
            passwordInput.focus();
        });
    }

    // 2. Remember Username Client Handling
    const usernameInput = document.getElementById('id_username');
    const rememberCheckbox = document.getElementById('id_remember_username');
    const STORAGE_KEY = 'regulax_remembered_user';

    // If there is stored username in localStorage and input is empty, populate it
    if (usernameInput && !usernameInput.value) {
        const savedUser = localStorage.getItem(STORAGE_KEY);
        if (savedUser) {
            usernameInput.value = savedUser;
            if (rememberCheckbox) {
                rememberCheckbox.checked = true;
            }
        }
    }

    // On form submission, sync localStorage with remember checkbox
    const loginForm = document.querySelector('.login-form');
    const submitBtn = document.getElementById('signInSubmitBtn');

    if (loginForm) {
        loginForm.addEventListener('submit', function () {
            if (rememberCheckbox && usernameInput) {
                if (rememberCheckbox.checked && usernameInput.value.trim()) {
                    localStorage.setItem(STORAGE_KEY, usernameInput.value.trim());
                } else if (!rememberCheckbox.checked) {
                    localStorage.removeItem(STORAGE_KEY);
                }
            }

            if (submitBtn) {
                // Prevent duplicate submission while keeping button legible
                setTimeout(function () {
                    submitBtn.disabled = true;
                    submitBtn.innerText = 'Signing in...';
                }, 50);
            }
        });
    }

    // 3. Modals for Forgot Password, Help, and Privacy
    const modalBackdrop = document.getElementById('infoModalBackdrop');
    const modalTitle = document.getElementById('modalTitle');
    const modalMessage = document.getElementById('modalMessage');
    const modalCloseBtn = document.getElementById('modalCloseBtn');
    const modalActionBtn = document.getElementById('modalActionBtn');

    const forgotPasswordLink = document.getElementById('forgotPasswordLink');
    const helpLink = document.getElementById('helpLink');
    const privacyLink = document.getElementById('privacyLink');

    function showModal(title, message) {
        if (!modalBackdrop) return;
        modalTitle.textContent = title;
        modalMessage.innerHTML = message;
        modalBackdrop.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }

    function closeModal() {
        if (!modalBackdrop) return;
        modalBackdrop.style.display = 'none';
        document.body.style.overflow = '';
    }

    if (modalCloseBtn) modalCloseBtn.addEventListener('click', closeModal);
    if (modalActionBtn) modalActionBtn.addEventListener('click', closeModal);
    if (modalBackdrop) {
        modalBackdrop.addEventListener('click', function (e) {
            if (e.target === modalBackdrop) {
                closeModal();
            }
        });
    }

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && modalBackdrop && modalBackdrop.style.display === 'flex') {
            closeModal();
        }
    });

    if (forgotPasswordLink) {
        forgotPasswordLink.addEventListener('click', function (e) {
            e.preventDefault();
            showModal(
                'Password Assistance',
                'In accordance with 21 CFR Part 11 and quality audit guidelines, password resets cannot be performed self-service without prior administrator clearance. Please contact your internal system administrator or laboratory supervisor to issue a temporary secure token.'
            );
        });
    }

    if (helpLink) {
        helpLink.addEventListener('click', function (e) {
            e.preventDefault();
            showModal(
                'Help & Support',
                'Need assistance accessing Regulax AI or navigating electronic signatures?<br><br>Please contact the Quality Assurance & IT Systems Desk at <strong>support@regulax.ai</strong> or consult your standard operating procedures (SOP-REG-014).'
            );
        });
    }

    if (privacyLink) {
        privacyLink.addEventListener('click', function (e) {
            e.preventDefault();
            showModal(
                'Privacy & Audit Policy',
                'All activity within Regulax AI, including sign-in attempts, approvals, electronic signatures, and session timestamps, are immutably logged to an audited ledger compliant with FDA 21 CFR Part 11 and ISO 9001 quality management requirements.'
            );
        });
    }
});

