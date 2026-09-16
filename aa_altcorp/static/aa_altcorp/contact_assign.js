/* Type-to-search assignment of an Auth account to a contact.
 *
 * The mains list is install-wide and the contacts table is unpaginated, so the
 * options are fetched on demand rather than rendered into every row.
 */
(function () {
    'use strict';

    var searchUrl = document.currentScript.dataset.searchUrl;
    var DEBOUNCE_MS = 200;

    function setupForm(form) {
        var query = form.querySelector('.altcorp-assign-query');
        var userId = form.querySelector('.altcorp-assign-user');
        var submit = form.querySelector('.altcorp-assign-submit');
        var results = form.querySelector('.altcorp-assign-results');
        var timer = null;

        function closeResults() {
            results.classList.remove('show');
            results.innerHTML = '';
        }

        function clearSelection() {
            // Editing the text invalidates any earlier pick, so the form must not
            // stay armed with a user that no longer matches what is on screen.
            userId.value = '';
            submit.disabled = true;
        }

        function choose(main) {
            userId.value = main.user_id;
            query.value = main.character_name;
            submit.disabled = false;
            closeResults();
        }

        function render(mains) {
            results.innerHTML = '';
            if (!mains.length) {
                var empty = document.createElement('span');
                empty.className = 'dropdown-item-text text-muted';
                empty.textContent = 'No mains found.';
                results.appendChild(empty);
            }
            mains.forEach(function (main) {
                var option = document.createElement('button');
                option.type = 'button';
                option.className = 'dropdown-item';
                option.textContent = main.corporation_name
                    ? main.character_name + ' — ' + main.corporation_name
                    : main.character_name;
                option.addEventListener('click', function () {
                    choose(main);
                });
                results.appendChild(option);
            });
            results.classList.add('show');
        }

        function search() {
            var term = query.value.trim();
            if (!term) {
                closeResults();
                return;
            }
            fetch(searchUrl + '?q=' + encodeURIComponent(term), {
                headers: {'X-Requested-With': 'XMLHttpRequest'}
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('Main character search failed: ' + response.status);
                    }
                    return response.json();
                })
                .then(function (data) {
                    render(data.results || []);
                })
                .catch(function () {
                    closeResults();
                });
        }

        query.addEventListener('input', function () {
            clearSelection();
            window.clearTimeout(timer);
            timer = window.setTimeout(search, DEBOUNCE_MS);
        });

        return {form: form, closeResults: closeResults};
    }

    document.addEventListener('DOMContentLoaded', function () {
        var forms = Array.prototype.map.call(
            document.querySelectorAll('.altcorp-assign'),
            setupForm
        );

        // One delegated listener rather than one per row: the contacts table is
        // unpaginated, so this used to add a document listener per contact.
        document.addEventListener('click', function (event) {
            forms.forEach(function (entry) {
                if (!entry.form.contains(event.target)) {
                    entry.closeResults();
                }
            });
        });
    });
})();
