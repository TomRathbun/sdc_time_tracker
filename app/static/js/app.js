/* SDC Time Tracker — Minimal JS helpers */

document.addEventListener('DOMContentLoaded', () => {
    // Auto-dismiss flash error/success messages after 5 seconds
    // Only targets elements with the 'flash-message' class (NOT modal elements)
    document.querySelectorAll('.flash-message').forEach(el => {
        setTimeout(() => {
            el.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
            el.style.opacity = '0';
            el.style.transform = 'translateY(-8px)';
            setTimeout(() => el.remove(), 500);
        }, 5000);
    });

    // Highlight active nav link
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach(link => {
        const href = link.getAttribute('href');
        if (href === currentPath || (href !== '/' && currentPath.startsWith(href))) {
            link.classList.add('text-white', 'bg-white/5');
            link.classList.remove('text-gray-400');
        }
    });

    // Live clock on dashboard (updates every second)
    const clockEl = document.getElementById('live-clock');
    if (clockEl) {
        setInterval(() => {
            const now = new Date();
            clockEl.textContent = now.toLocaleTimeString('en-GB', {
                hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
        }, 1000);
    }

    // Form submit loading state
    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', () => {
            const btn = form.querySelector('button[type="submit"]');
            if (btn) {
                btn.disabled = true;
                btn.style.opacity = '0.7';
                const originalText = btn.innerHTML;
                btn.innerHTML = `
                    <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    Processing...
                `;
                // Re-enable after 10s in case of error
                setTimeout(() => {
                    btn.disabled = false;
                    btn.style.opacity = '1';
                    btn.innerHTML = originalText;
                }, 10000);
            }
        });
    });

    // ═══════════════════════════════════════════
    //  Multi-Timezone Clock Updater
    // ═══════════════════════════════════════════
    var tzZones = [
        { id: 'pacific', tz: 'America/Los_Angeles' },
        { id: 'mountain', tz: 'America/Denver' },
        { id: 'central', tz: 'America/Chicago' },
        { id: 'eastern', tz: 'America/New_York' },
        { id: 'thailand', tz: 'Asia/Bangkok' },
        { id: 'utc', tz: 'UTC' },
        { id: 'abudhabi', tz: 'Asia/Dubai' },
        { id: 'manila', tz: 'Asia/Manila' },
        { id: 'tokyo', tz: 'Asia/Tokyo' },
        { id: 'sydney', tz: 'Australia/Sydney' }
    ];

    function updateTZClocks() {
        if (!document.getElementById('tz-clocks')) return;
        var now = new Date();
        tzZones.forEach(function (zone) {
            var opts = { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' };
            var dateOpts = { weekday: 'short', month: 'short', day: 'numeric' };
            if (zone.tz) {
                opts.timeZone = zone.tz;
                dateOpts.timeZone = zone.tz;
            }
            var timeStr = now.toLocaleTimeString('en-US', opts);
            var dateStr = now.toLocaleDateString('en-US', dateOpts);

            var parts = timeStr.split(':');
            var formatted = parts[0] + ':' + parts[1] + ':<span class="tz-seconds">' + parts[2] + '</span>';

            var clockEl = document.getElementById('clock-' + zone.id);
            var dateEl = document.getElementById('date-' + zone.id);
            if (clockEl) clockEl.innerHTML = formatted;
            if (dateEl) dateEl.textContent = dateStr;
        });
    }

    // Run automatically
    updateTZClocks();
    setInterval(updateTZClocks, 1000);
});
