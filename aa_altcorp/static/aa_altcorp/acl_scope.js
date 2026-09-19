(function () {
    document.querySelectorAll('.acl-scope-form').forEach(function (form) {
        let timer;
        const broadScope = form.querySelector('.acl-broad-scope');
        form.querySelectorAll('.acl-entity-picker').forEach(function (picker) {
            const input = picker.querySelector('.acl-entity-query');
            const resultBox = picker.querySelector('.acl-entity-results');
            const chips = picker.querySelector('.acl-entity-chips');

            function addEntity(item) {
                if (chips.querySelector('[data-id="' + item.id + '"]')) return;
                const chip = document.createElement('span');
                chip.className = 'badge text-bg-secondary me-1 mb-1';
                chip.dataset.id = item.id;
                chip.append(item.name + ' (' + item.id + ') ');
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'btn-close btn-close-white';
                remove.setAttribute('aria-label', 'Remove');
                remove.addEventListener('click', () => chip.remove());
                chip.appendChild(remove);
                const hidden = document.createElement('input');
                hidden.type = 'hidden';
                hidden.name = input.dataset.field;
                hidden.value = item.id;
                chip.appendChild(hidden);
                chips.appendChild(chip);
                broadScope.checked = false;
                input.value = '';
                resultBox.classList.remove('show');
            }

            input.addEventListener('input', function () {
                if (input.value.trim()) broadScope.checked = false;
                clearTimeout(timer);
                const query = input.value.trim();
                if (query.length < 2) {
                    resultBox.classList.remove('show');
                    return;
                }
                timer = setTimeout(function () {
                    const url = form.dataset.searchUrl + '?type=' + encodeURIComponent(input.dataset.entityType) + '&q=' + encodeURIComponent(query);
                    fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
                        .then(response => response.json())
                        .then(data => {
                            resultBox.innerHTML = '';
                            data.results.forEach(function (item) {
                                const option = document.createElement('button');
                                option.type = 'button';
                                option.className = 'dropdown-item';
                                option.textContent = item.name + ' (' + item.id + ')';
                                option.addEventListener('click', () => addEntity(item));
                                resultBox.appendChild(option);
                            });
                            resultBox.classList.toggle('show', data.results.length > 0);
                        });
                }, 250);
            });
            chips.querySelectorAll('.btn-close').forEach(button => {
                button.addEventListener('click', () => button.closest('[data-id]').remove());
            });
        });
    });
})();
