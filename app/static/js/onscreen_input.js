/**
 * SDC Time Tracker — On-Screen Input Components
 *
 * Provides:
 *  1. NumPad  — for PIN entry fields (password inputs, numeric inputs)
 *  2. Keyboard — for text/comment fields (textareas and text inputs)
 *
 * Both components load their enabled/disabled state from /api/settings on page load.
 */

(function () {
    'use strict';

    let _numpadEnabled = false;
    let _keyboardEnabled = false;
    let _activeInput = null;

    // ── Fetch settings ───────────────────────────────────────────────
    async function loadSettings() {
        try {
            const res = await fetch('/api/settings');
            const data = await res.json();
            _numpadEnabled = !!data.onscreen_numpad_enabled;
            _keyboardEnabled = !!data.onscreen_keyboard_enabled;

            if (_numpadEnabled) initNumPad();
            if (_keyboardEnabled) initKeyboard();
        } catch (e) {
            console.warn('Could not load input settings:', e);
        }
    }

    /** Check if an element is a numeric/PIN input */
    function isNumericInput(el) {
        if (el.tagName !== 'INPUT') return false;
        if (el.dataset.oskIgnore !== undefined) return false;
        if (el.dataset.numpad !== undefined) return true;
        if (el.classList.contains('numpad-target')) return true;
        if (el.type === 'password') return true;
        if (el.inputMode === 'numeric') return true;
        const pat = el.getAttribute('pattern');
        if (pat && pat.includes('[0-9]')) return true;
        return false;
    }

    /** Check if an element is a text/comment input */
    function isTextInput(el) {
        if (el.tagName === 'TEXTAREA') {
            return el.dataset.oskIgnore === undefined;
        }
        if (el.tagName === 'INPUT') {
            if (el.dataset.oskIgnore !== undefined) return false;
            if (el.type === 'text' || el.type === 'search' || el.type === 'email' || el.type === 'url') {
                // Exclude numeric inputs
                if (isNumericInput(el)) return false;
                return true;
            }
        }
        return false;
    }

    // ── NUMPAD ───────────────────────────────────────────────────────
    function initNumPad() {
        const panel = document.createElement('div');
        panel.id = 'osk-numpad';
        panel.className = 'osk-panel osk-numpad';
        panel.innerHTML = `
            <div class="osk-header">
                <span class="osk-title">Number Pad</span>
                <button class="osk-close" data-osk-close="numpad">&times;</button>
            </div>
            <div class="osk-numpad-grid">
                <button class="osk-key osk-key-num" data-key="1">1</button>
                <button class="osk-key osk-key-num" data-key="2">2</button>
                <button class="osk-key osk-key-num" data-key="3">3</button>
                <button class="osk-key osk-key-num" data-key="4">4</button>
                <button class="osk-key osk-key-num" data-key="5">5</button>
                <button class="osk-key osk-key-num" data-key="6">6</button>
                <button class="osk-key osk-key-num" data-key="7">7</button>
                <button class="osk-key osk-key-num" data-key="8">8</button>
                <button class="osk-key osk-key-num" data-key="9">9</button>
                <button class="osk-key osk-key-action osk-key-clear" data-key="clear">C</button>
                <button class="osk-key osk-key-num" data-key="0">0</button>
                <button class="osk-key osk-key-action osk-key-back" data-key="back">&#9003;</button>
            </div>`;
        document.body.appendChild(panel);

        // CRITICAL: Prevent buttons from stealing focus from the input
        panel.addEventListener('mousedown', (e) => {
            e.preventDefault();
        });
        panel.addEventListener('touchstart', (e) => {
            // Don't prevent default on close button
            if (e.target.dataset.oskClose) return;
            e.preventDefault();
        }, { passive: false });

        // Close button
        panel.querySelector('[data-osk-close]').addEventListener('click', () => {
            panel.classList.remove('osk-visible');
        });

        // Bind numpad keys
        panel.querySelectorAll('.osk-key').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                if (!_activeInput) return;
                const key = btn.dataset.key;
                if (key === 'clear') {
                    _activeInput.value = '';
                } else if (key === 'back') {
                    _activeInput.value = _activeInput.value.slice(0, -1);
                } else {
                    const maxLen = _activeInput.maxLength > 0 ? _activeInput.maxLength : 20;
                    if (_activeInput.value.length < maxLen) {
                        _activeInput.value += key;
                    }
                }
                _activeInput.dispatchEvent(new Event('input', { bubbles: true }));
                // Re-focus the input to keep cursor blinking
                _activeInput.focus();
            });
        });

        // Show numpad when a numeric input gets focus
        document.addEventListener('focusin', (e) => {
            if (!_numpadEnabled) return;
            if (isNumericInput(e.target)) {
                _activeInput = e.target;
                // Hide keyboard if open
                const kb = document.getElementById('osk-keyboard');
                if (kb) kb.classList.remove('osk-visible');
                panel.classList.add('osk-visible');
            }
        });

        // Hide numpad when focus leaves a numeric input (but not to the panel)
        document.addEventListener('focusout', (e) => {
            setTimeout(() => {
                const active = document.activeElement;
                // If focus moved to another numeric input, keep showing
                if (active && isNumericInput(active)) return;
                // If focus is inside the panel, keep showing
                if (active && panel.contains(active)) return;
                // Otherwise hide
                panel.classList.remove('osk-visible');
            }, 150);
        });

        // If a numeric input is already focused (e.g. autofocus on login page),
        // show the numpad immediately
        const currentlyFocused = document.activeElement;
        if (currentlyFocused && isNumericInput(currentlyFocused)) {
            _activeInput = currentlyFocused;
            panel.classList.add('osk-visible');
        }
    }

    // ── KEYBOARD ─────────────────────────────────────────────────────
    function initKeyboard() {
        const panel = document.createElement('div');
        panel.id = 'osk-keyboard';
        panel.className = 'osk-panel osk-keyboard';

        const rows = [
            ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
            ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
            ['SHIFT', 'z', 'x', 'c', 'v', 'b', 'n', 'm', 'BACK'],
            ['123', 'SPACE', '.', 'DONE'],
        ];

        const numRows = [
            ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
            ['-', '/', ':', ';', '(', ')', '$', '&', '@', '"'],
            ['#+=', '.', ',', '?', '!', "'", 'BACK'],
            ['ABC', 'SPACE', '.', 'DONE'],
        ];

        let _shifted = false;
        let _numMode = false;

        function renderRows(rowSet) {
            return rowSet.map(row => {
                const keys = row.map(k => {
                    let cls = 'osk-key';
                    let label = k;
                    let dataKey = k.toLowerCase();

                    if (k === 'SHIFT') { cls += ' osk-key-action osk-key-wide'; label = '⇧'; dataKey = 'shift'; }
                    else if (k === 'BACK') { cls += ' osk-key-action osk-key-wide'; label = '⌫'; dataKey = 'back'; }
                    else if (k === 'SPACE') { cls += ' osk-key-space'; label = 'space'; dataKey = 'space'; }
                    else if (k === 'DONE') { cls += ' osk-key-done'; label = 'Done'; dataKey = 'done'; }
                    else if (k === '123') { cls += ' osk-key-action'; dataKey = '123'; }
                    else if (k === 'ABC') { cls += ' osk-key-action'; dataKey = 'abc'; }
                    else if (k === '#+=') { cls += ' osk-key-action'; dataKey = '#+='; }
                    else {
                        label = _shifted ? k.toUpperCase() : k;
                    }
                    return `<button class="${cls}" data-key="${dataKey}" type="button">${label}</button>`;
                }).join('');
                return `<div class="osk-row">${keys}</div>`;
            }).join('');
        }

        function render() {
            const rowSet = _numMode ? numRows : rows;
            panel.innerHTML = `
                <div class="osk-header">
                    <span class="osk-title">Keyboard</span>
                    <button class="osk-close" data-osk-close="keyboard">&times;</button>
                </div>
                <div class="osk-keyboard-grid">
                    ${renderRows(rowSet)}
                </div>`;

            // Close button
            panel.querySelector('[data-osk-close]').addEventListener('click', () => {
                panel.classList.remove('osk-visible');
            });

            // Bind keyboard keys
            panel.querySelectorAll('.osk-key').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    if (!_activeInput) return;
                    const key = btn.dataset.key;

                    if (key === 'shift') {
                        _shifted = !_shifted;
                        render();
                    } else if (key === 'back') {
                        _activeInput.value = _activeInput.value.slice(0, -1);
                    } else if (key === 'space') {
                        _activeInput.value += ' ';
                    } else if (key === 'done') {
                        panel.classList.remove('osk-visible');
                        _activeInput.blur();
                    } else if (key === '123') {
                        _numMode = true;
                        render();
                    } else if (key === 'abc') {
                        _numMode = false;
                        render();
                    } else if (key === '#+=') {
                        _numMode = false;
                        render();
                    } else {
                        const char = _shifted ? key.toUpperCase() : key;
                        _activeInput.value += char;
                        if (_shifted) {
                            _shifted = false;
                            render();
                        }
                    }
                    _activeInput.dispatchEvent(new Event('input', { bubbles: true }));
                    if (key !== 'done') _activeInput.focus();
                });
            });
        }

        document.body.appendChild(panel);
        render();

        // CRITICAL: Prevent buttons from stealing focus
        panel.addEventListener('mousedown', (e) => {
            e.preventDefault();
        });
        panel.addEventListener('touchstart', (e) => {
            if (e.target.dataset.oskClose) return;
            e.preventDefault();
        }, { passive: false });

        // Show keyboard when a text input gets focus
        document.addEventListener('focusin', (e) => {
            if (!_keyboardEnabled) return;
            if (isTextInput(e.target)) {
                _activeInput = e.target;
                // Hide numpad if open
                const np = document.getElementById('osk-numpad');
                if (np) np.classList.remove('osk-visible');
                panel.classList.add('osk-visible');
            }
        });

        // Hide keyboard when focus leaves text inputs
        document.addEventListener('focusout', (e) => {
            setTimeout(() => {
                const active = document.activeElement;
                if (active && isTextInput(active)) return;
                if (active && panel.contains(active)) return;
                panel.classList.remove('osk-visible');
            }, 150);
        });
    }

    // ── Initialize on DOM ready ──────────────────────────────────────
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', loadSettings);
    } else {
        loadSettings();
    }
})();
