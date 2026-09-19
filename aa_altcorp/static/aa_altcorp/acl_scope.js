(function () {
    document.querySelectorAll('.acl-scope-form').forEach(function (form) {
        const resultBox = form.querySelector('.acl-entity-results');
        let timer;
        form.querySelectorAll('.acl-entity-query').forEach(function (input) {
            input.addEventListener('input', function () {
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
                                option.addEventListener('click', function () {
                                    input.value = item.id;
                                    resultBox.classList.remove('show');
                                });
                                resultBox.appendChild(option);
                            });
                            resultBox.classList.toggle('show', data.results.length > 0);
                        });
                }, 250);
            });
        });
    });
})();
