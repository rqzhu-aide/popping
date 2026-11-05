// Public pages need the site controls, without the classroom application.
(() => {
    function updateThemeIcon() {
        const icon = document.getElementById('theme-icon');
        if (icon) {
            icon.textContent = document.documentElement.classList.contains('light')
                ? '☽' : '☀';
        }
    }

    window.toggleTheme = function() {
        const light = document.documentElement.classList.toggle('light');
        try {
            localStorage.setItem('popping-theme', light ? 'light' : 'dark');
        } catch (error) {
            // The theme still changes when storage is unavailable.
        }
        updateThemeIcon();
    };
    updateThemeIcon();

    const landing = document.querySelector('.landing');
    if (landing && typeof window.matchMedia === 'function') {
        const intro = landing.querySelector('.intro-card');
        const courses = landing.querySelector('.course-selection');
        const compact = window.matchMedia('(max-width: 600px)');
        const syncLandingOrder = () => {
            const first = compact.matches ? courses : intro;
            const second = compact.matches ? intro : courses;
            if (landing.firstElementChild === first) return;

            // Move the other section so reparenting cannot blur the active link.
            if (first.contains(document.activeElement)) {
                landing.append(second);
            } else {
                landing.insertBefore(first, second);
            }
        };
        syncLandingOrder();
        compact.addEventListener('change', syncLandingOrder);
    }

    let navigating = false;
    window.beginPageNavigation = function(form) {
        if (navigating) return false;
        navigating = true;
        form?.querySelectorAll('button[type="submit"]').forEach(button => {
            button.disabled = true;
        });
        return true;
    };
    window.addEventListener('pageshow', () => {
        navigating = false;
        document.querySelectorAll('[data-public-navigation] button[type="submit"]')
            .forEach(button => { button.disabled = false; });
    });

    window.confirmDemoReset = function(form) {
        if (!confirm('Reset demo data? This will restore everything to its initial state.')) {
            return false;
        }
        const slug = form?.querySelector('[data-course-slug]')?.dataset.courseSlug ||
            document.querySelector('.demo-banner-reset')?.dataset.courseSlug;
        try {
            if (slug) localStorage.removeItem(`popping-session-start:${slug}`);
            localStorage.removeItem('popping-session-start');
        } catch (error) {
            // Reset remains available when storage is blocked.
        }
        return window.beginPageNavigation(form);
    };

    document.querySelectorAll('.demo-role-form, .demo-actions, .demo-banner-exit-form, .demo-banner-reset-form')
        .forEach(form => {
            form.dataset.publicNavigation = '';
            if (!form.hasAttribute('onsubmit')) {
                form.addEventListener('submit', event => {
                    if (!window.beginPageNavigation(form)) event.preventDefault();
                });
            }
        });

    const displayName = document.getElementById('display_name');
    const clearDisplayName = document.getElementById('clear_display_name');
    if (displayName && clearDisplayName) {
        const syncDisplayName = () => {
            displayName.disabled = clearDisplayName.checked;
            displayName.setAttribute('aria-disabled', String(clearDisplayName.checked));
            if (clearDisplayName.checked) displayName.value = '';
        };
        clearDisplayName.addEventListener('change', syncDisplayName);
        displayName.addEventListener('input', () => {
            if (!displayName.value.trim()) return;
            clearDisplayName.checked = false;
            syncDisplayName();
        });
        syncDisplayName();
    }

    try {
        const message = sessionStorage.getItem('popping-flash-message');
        if (message) {
            sessionStorage.removeItem('popping-flash-message');
            const notice = document.createElement('div');
            notice.className = 'flash flash-success';
            notice.setAttribute('role', 'status');
            notice.textContent = message;
            document.querySelector('main.container')?.prepend(notice);
        }
    } catch (error) {
        // Public navigation also works when storage is blocked.
    }
})();
